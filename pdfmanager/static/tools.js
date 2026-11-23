/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// ---- files -------------------------------------------------------------------
function addFiles(list) {
  const want = accepts(current.files);
  const wanted = [...list].filter(want), rejected = list.length - wanted.length;
  // A file already in the list isn't added twice.
  const accepted = wanted.filter((f, i) => !items.some((it) => sameFile(it.file, f)) && !wanted.slice(0, i).some((g) => sameFile(g, f)));
  const twice = wanted.length - accepted.length;
  carried = null;  // (the list is the user's own now)
  for (const file of accepted) {
    const it = { file, id: Math.random().toString(36).slice(2), pages: null, thumb: null, locked: false, loading: true };
    items.push(it);
    if (isImage(file)) { it.thumb = URL.createObjectURL(file); it.loading = false; }
    else if (!isPdf(file)) it.loading = false;  // Office files: no preview
    else inspect(it);
  }
  renderFiles();
  clearMessages();
  if (twice && !rejected) appToast(twice === 1 ? "That file is already in the list." : `${plural(twice, "file")} were already in the list.`);
  if (rejected) showError(`${plural(rejected, "file")} skipped — only ${{ images: "images", office: "Word, Excel, PowerPoint, OpenDocument, RTF, text or web page files", web: "saved web pages (.html, .mht)" }[current.files] || "PDFs"} can be used here.`);
}

async function inspect(it) {
  it.loading = true;
  const body = new FormData();
  body.append("file", it.file, it.file.name);
  body.append("password", $("pw").value);
  try {
    const info = await (await api("/api/inspect", { method: "POST", body })).json();
    Object.assign(it, { pages: info.pages, thumb: info.thumb, locked: info.locked, error: info.error, for_people: !!info.for_people });
  } catch (e) { it.error = e.message; }
  it.loading = false;
  renderFiles();
}

$("pw-toggle").addEventListener("click", () => {
  const show = $("pw").type === "password";
  $("pw").type = show ? "text" : "password";
  $("pw-toggle").setAttribute("aria-pressed", String(show));
  $("pw-toggle").textContent = show ? "Hide" : "Show";
});
// The sticky action bar's height, for scroll-padding: what gets focus stops above it.
{
  const bar = document.querySelector(".action-bar");
  const keep = () => document.documentElement.style.setProperty("--action-bar-h", `${bar.offsetHeight}px`);
  if (window.ResizeObserver) new ResizeObserver(keep).observe(bar);
  keep();
}
// Skip link: to the tool's page, or to the page view in the reader.
$("skip-link").addEventListener("click", (e) => {
  e.preventDefault();  // (the address keeps its #token)
  if (inReader() && reader && reader.doc) { reader.container.focus(); return; }
  const main = document.querySelector("main");
  main.focus({ preventScroll: true });
  main.scrollIntoView({ block: "start" });
});
let pwTimer = null;
$("pw").addEventListener("input", () => {
  clearTimeout(pwTimer);
  pwTimer = setTimeout(() => { items.filter((it) => it.locked).forEach(inspect); syncBoard(); syncPanel(); }, 400);
});

let dragId = null;
function renderFiles() {
  const grid = $("file-grid");
  grid.replaceChildren();
  const ordered = current && (current.id === "merge" || current.files === "pdf2" || current.files === "images");
  items.forEach((it, i) => {
    const thumb = el("div", { class: "thumb" });
    if (it.loading) thumb.append(el("span", { class: "loading", role: "img", "aria-label": "Loading preview" }));
    else if (it.thumb) thumb.append(el("img", { src: it.thumb, alt: "" }));
    else thumb.append(icon(it.locked ? "lock" : "file"));
    const sub = el("div", { class: "file-sub" });
    if (it.locked) sub.append(el("span", { class: "badge warn" }, "Locked"));
    else if (it.for_people) sub.append(el("span", { class: "badge warn", title: "It opens only with the Digital ID of one of the people it was protected for" }, "For particular people"));
    else if (it.error) sub.append(el("span", { class: "badge warn" }, "Unreadable"));
    else if (it.pages != null) sub.append(el("span", {}, plural(it.pages, "page")), el("span", {}, "·"));
    sub.append(el("span", {}, humanSize(it.file.size)));
    const card = el("div", { class: "file-card", draggable: "true", title: it.file.name },
      thumb,
      el("div", { class: "file-meta" }, el("div", { class: "file-name" }, it.file.name), sub),
      ordered ? el("span", { class: "order" }, String(i + 1)) : null,
      el("div", { class: "card-actions" },
        isPdf(it.file) ? el("button", { class: "chip-btn", type: "button", title: "Open in reader", "aria-label": `Open ${it.file.name} in the reader`, onclick: () => openInReader(it.file) }, icon("reader")) : null,
        items.length > 1 ? el("button", { class: "chip-btn", type: "button", title: "Move earlier", "aria-label": `Move ${it.file.name} earlier`, disabled: i === 0, onclick: () => move(i, i - 1) }, icon("left")) : null,
        items.length > 1 ? el("button", { class: "chip-btn", type: "button", title: "Move later", "aria-label": `Move ${it.file.name} later`, disabled: i === items.length - 1, onclick: () => move(i, i + 1) }, icon("right")) : null,
        el("button", { class: "chip-btn", type: "button", title: "Remove", "aria-label": `Remove ${it.file.name}`, onclick: () => { items.splice(i, 1); carried = null; renderFiles(); } }, icon("x"))));
    card.addEventListener("dragstart", (e) => { dragId = it.id; card.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", it.id); });
    card.addEventListener("dragend", () => { dragId = null; card.classList.remove("dragging"); });
    card.addEventListener("dragover", (e) => { if (dragId) { e.preventDefault(); card.classList.add("drop-before"); } });
    card.addEventListener("dragleave", () => card.classList.remove("drop-before"));
    card.addEventListener("drop", (e) => {
      card.classList.remove("drop-before");
      if (!dragId) return;
      e.preventDefault(); e.stopPropagation();
      move(items.findIndex((x) => x.id === dragId), i);
    });
    grid.append(card);
  });
  grid.hidden = items.length === 0;
  if (!items.length) carried = null;
  showCarried();
  // One or two files: a compact strip (thumbnail, name, facts, actions); three or more: the grid.
  grid.classList.toggle("strip", items.length > 0 && items.length <= 2);
  $("drop").classList.toggle("compact", items.length > 0);
  syncDropFirst();
  const pages = items.reduce((n, it) => n + (it.pages || 0), 0);
  const size = items.reduce((n, it) => n + it.file.size, 0);
  $("files-sub").textContent = items.length ? `${plural(items.length, "file")}${pages ? " · " + plural(pages, "page") : ""} · ${humanSize(size)}` : "";
  updateSummary();
  syncBoard();
  scheduleLive(0);
  syncPanel();
}
/** Files the page couldn't open (Repair is for those, so it takes them). */
function unreadable() {
  if (!current || current.id === "repair") return [];
  // (One protected for particular people is what Open with Digital ID is for.)
  return items.filter((it) => it.error && !it.locked && !it.loading && !(it.for_people && current.id === "decrypt-cert"));
}
function move(from, to) {
  if (to < 0 || to >= items.length || from === to) return;
  const [it] = items.splice(from, 1); items.splice(to, 0, it); renderFiles();
}
function updateSummary() {
  if (!current || current.files === "home" || current.files === "scan") return;
  $("kbd-hint").textContent = "";
  if (current.files === null) {
    $("summary").textContent = current.preview === false ? "Nothing on your computer is changed." : "Preview first — nothing changes until you click Apply.";
    // (A folder tool needs no file: don't keep the last tool's "Add a file first".)
    if (!$("run").classList.contains("busy") && !$("preview").classList.contains("busy")) { $("run").disabled = false; $("run").title = ""; }
    return;
  }
  const need = current.files === "pdf2" ? 2 : current.min || 1;
  // A tool that can work from an address instead (Web page → PDF).
  const typed = current.url_field && ($("f-" + current.url_field)?.value || "").trim();
  const bad = unreadable(), usable = items.length - bad.length;
  const ready = usable >= need || !!typed;
  const done = ready && !$("result").hidden;
  // Redact: nothing to black out chosen yet (words, kinds of numbers, or areas drawn).
  const choose = ready && !done && current.id === "redact" && !redactChosen();
  const badSay = bad.length === 1 ? `${bad[0].file.name} can't be opened` : `${bad.length} files can't be opened`;
  // Protected for particular people: not damaged, and Repair is no help.
  const people = bad.length && bad.every((it) => it.for_people);
  $("summary").textContent = done ? "Done · change anything and run again, or start over"
    : choose ? "Choose what to black out: words, kinds of numbers, or areas on the page"
    : ready ? `Ready · ${plural(usable, "file")}` + (bad.length ? ` · ${badSay} and will be left out` : "")
    : people ? `${bad.length === 1 ? bad[0].file.name + " is" : "These files are"} protected for particular people: open ${bad.length === 1 ? "it" : "them"} with your Digital ID first (Protect → Open with Digital ID).`
    : bad.length ? `${badSay} — ${bad.length === 1 ? "it" : "they"} may be damaged. Try Repair, or remove ${bad.length === 1 ? "it" : "them"}.`
    : (need === 1 ? "Add a file to get started" : `Add ${current.files === "pdf2" ? "exactly 2" : "at least " + need} files`);
  // A file that can't be opened is said once (a polite status), not again at every repaint.
  const badNote = bad.length && !done ? $("summary").textContent : "";
  if ($("summary-live").textContent !== badNote) $("summary-live").textContent = badNote;
  $("kbd-hint").textContent = ready && !done && !choose ? ` · ${isMac ? "⌘" : "Ctrl"}+Enter to run` : "";
  $("run-label").textContent = (current.action || "Run") + (done ? " again" : "");
  // Nothing to run on yet: the button waits (the line beside it says why).
  if (!$("run").classList.contains("busy") && !$("preview").classList.contains("busy")) {
    $("run").disabled = !ready || choose;
    $("run").title = choose ? "Choose what to black out first" : ready ? "" : (need === 1 ? "Add a file first" : $("summary").textContent);
  }
}
/** Redact has something to black out: words or phrases, a kind of number, or areas drawn. */
function redactChosen() {
  if (($("f-terms")?.value || "").trim()) return true;
  if (($("f-regex")?.value || "").trim()) return true;
  if (["emails", "phones", "numbers", "ssn", "ein", "uk_ni", "iban", "credit_card"].some((k) => $("f-" + k)?.checked)) return true;
  try {
    const boxes = JSON.parse($("f-boxes")?.value || "null");
    return !!boxes && Object.values(boxes).some((list) => Array.isArray(list) ? list.length : !!list);
  } catch (e) { return false; }
}
$("form").addEventListener("input", () => { if (current?.id === "redact") updateSummary(); });
/** Back to an empty tool: no files, no result, options as they were. */
function startOver() {
  items = [];
  // What was drawn on the pages goes with the files.
  for (const id of ["f-fields", "f-edits", "f-boxes"]) if ($(id)) $(id).value = "";
  clearMessages();
  renderFiles();
  updateSummary();
  document.querySelector("main").scrollIntoView({ block: "start", behavior: smoothScroll() });
}

const drop = $("drop");
$("file-input").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
$("drop-choose").addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); $("file-input").click(); });
// The file box is hidden, so the drop area is the keyboard's way in: Enter or Space opens the chooser.
$("drop").addEventListener("keydown", (e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); $("file-input").click(); } });
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { if (hasFiles(e)) { e.preventDefault(); drop.classList.add("over"); } }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
drop.addEventListener("drop", (e) => { if (hasFiles(e)) { e.preventDefault(); e.stopPropagation(); addFiles(e.dataTransfer.files); } });
// Dropping files anywhere on the files card (or page) adds them too.
document.addEventListener("dragover", (e) => { if (hasFiles(e) && current && current.files !== null) e.preventDefault(); });
document.addEventListener("drop", (e) => {
  if (!hasFiles(e) || !current || current.files === null) return;
  e.preventDefault();
  if (inReader()) {  // dropped outside the reader pane (e.g. on the rail)
    const pdf = [...e.dataTransfer.files].find(isPdf);
    if (pdf) reader.openFile(pdf);
  } else if (current.files === "home") openFilesInReader(e.dataTransfer.files);
  else if (current.files === "scan") scanAddPhotos(e.dataTransfer.files);
  else addFiles(e.dataTransfer.files);
});

// ---- Home ----------------------------------------------------------------------------
// The start screen: open or drop a PDF to read it, pinned tools, recent files
// (with what ZPDF knows about each) and the tools that start from several files.
// TAGLINES give each tool its line in the rail's flyout and the palette.
const TAGLINES = {
  reader: "Read, annotate and sign PDFs", merge: "Combine several PDFs into one", split: "Break a PDF into separate files",
  interleave: "Weave scanned fronts and backs together", compare: "See what changed between two versions",
  extract: "Pull out just the pages you need", delete: "Take pages out", reorder: "Drag pages into a new order",
  "insert-blank": "Add empty pages, e.g. for notes", booklet: "Print a folded, stapled booklet", autocrop: "Cut away white borders automatically",
  reverse: "Flip the page order", bookmarks: "Add and edit the outline in the sidebar", rotate: "Turn pages the right way up",
  resize: "Change the paper size or scale pages", crop: "Trim the margins", nup: "Several pages on one sheet",
  compress: "Make files smaller", color: "Grayscale, RGB or CMYK", ocr: "Make scans searchable",
  clean: "Drop blank pages and straighten scans", watermark: "Stamp text or a logo on every page", number: "Add page numbers",
  flatten: "Bake forms and comments into the page", "flatten-layers": "Keep a drawing's shown layers, drop the hidden ones", headers: "Titles, dates and Bates numbers", form: "Create fillable forms",
  metadata: "Set the title, author and keywords", encrypt: "Protect with a password", decrypt: "Remove a password",
  redact: "Black out text for good", sanitize: "Remove hidden data and scripts", makesafe: "Take out what could do harm in a PDF you were sent", "sec-profile": "Apply a saved bundle of security rules in one step", "upgrade-protection": "Replace old, weak encryption with AES-256",
  "encrypt-cert": "Only the people you choose can open it, with their Digital ID", "decrypt-cert": "Open a PDF that was protected for you", pdfa: "Convert for long-term archiving", "pdfa-check": "See if a file meets PDF/A, and why not",
  comments: "List comments, or send them as XFDF", web: "Save a web page as a PDF", attach: "Attach files to a PDF, or remove them", labels: "Number pages i, ii, iii then 1, 2, 3", repair: "Mend damaged files; open fast online",
  "sign-cert": "Sign with a certificate (.pfx or .p12), like Acrobat", "verify-sig": "See who signed and if anything changed",
  tag: "Tag for screen readers, like Acrobat's Autotag", "access-check": "See what keeps screen readers out, and how to fix it",  // -- accessibility --
  scan: "Scanner or phone photos to a searchable PDF", "from-images": "Turn photos and scans into a PDF", "to-images": "Save pages as PNG or JPG", text: "Get the text out",
  media: "Extract pictures and attached files", "to-word": "Convert to an editable Word file", "from-office": "Turn Office documents into PDFs",
  "to-excel": "Put tables into a spreadsheet",
  "quantities": "Total up what was measured on a drawing",
  /* -- pptx and html -- */
  "to-pptx": "Turn pages into editable slides", "to-html": "Make a web page from a PDF",
  "to-md": "Convert PDFs to structured Markdown",
  /* -- end of pptx and html -- */
  recipe: "Run several tools in one go", organize: "Sort a folder into subfolders", rename: "Rename files from their details",
  duplicates: "Find identical PDFs", search: "Find words, or answer a question, across a folder",
};
const inHome = () => current && current.files === "home";
function recentTools() { try { return JSON.parse(localStorage.getItem("pdfm-recent-tools") || "[]"); } catch (e) { return []; } }
function rememberTool(id) {
  try { localStorage.setItem("pdfm-recent-tools", JSON.stringify([id, ...recentTools().filter((t) => t !== id)].slice(0, 6))); } catch (e) {}
}
async function openFilesInReader(list) {
  const pdfs = [...list].filter(isPdf);
  if (!pdfs.length) { showError("Drop PDF files to read them."); return; }
  await select("reader");
  for (const pdf of pdfs) await reader.openFile(pdf);
}
function whenText(t) {
  if (!t) return "";
  const d = new Date(t), days = Math.round((new Date(new Date().toDateString()) - new Date(d.toDateString())) / 86400000);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return days === 0 ? `Today ${time}` : days === 1 ? `Yesterday ${time}` : d.toLocaleDateString();
}
/** What ZPDF knows about a recent file, in plain words: "12 pages · 4.1 MB ·
 * scanned, no text yet". From the facts /api/recent sends (filefacts.py).
 * Self-contained, so tests can run it on its own. */
function factsLine(facts) {
  if (!facts) return "";
  if (facts.error) return "can't be read";
  if (facts.pages == null) return facts.protected ? (facts.for_people ? "for particular people" : facts.weak_protection ? "weak protection" : "protected") : "can't be read";
  const parts = [facts.pages + (facts.pages === 1 ? " page" : " pages")];
  const size = Number(facts.size);
  if (size >= 0 && facts.size != null) {
    const mb = size / 1048576;
    parts.push(mb >= 1024 ? (mb / 1024).toFixed(1) + " GB" : mb >= 1 ? mb.toFixed(1) + " MB" : Math.max(1, Math.round(size / 1024)) + " KB");
  }
  if (facts.scanned) parts.push(facts.has_text ? "scanned, searchable" : "scanned, no text yet");
  if (facts.form_fields > 0) parts.push("form, " + facts.form_fields + (facts.form_fields === 1 ? " field" : " fields"));
  if (facts.signatures > 0) parts.push("signed");
  if (facts.pdfa) parts.push("PDF/A");
  if (facts.protected) parts.push(facts.weak_protection ? "weak protection" : "protected");
  return parts.join(" · ");
}
const MULTI_TOOLS = [["merge", "Several PDFs into one"], ["compare", "What changed between two versions"], ["search", "Find words or ask a question across its PDFs", "Search a folder"]];
async function renderHome() {
  const host = $("home-host");
  const input = el("input", { type: "file", accept: ".pdf,application/pdf", multiple: true, hidden: true });
  input.addEventListener("change", () => { if (input.files.length) openFilesInReader(input.files); input.value = ""; });
  const drop = el("div", { class: "start-drop", role: "button", tabindex: "0", "aria-label": "Open PDFs to read" },
    icon("upload"),
    el("div", {}, el("b", {}, "Drop PDFs here"), ", or choose them from your computer.", el("br"), "Nothing leaves this PC."),
    el("div", { class: "start-acts" },
      el("button", { type: "button", class: "btn seal", onclick: (e) => { e.stopPropagation(); input.click(); } }, icon("reader"), "Open a PDF"),
      ops.some((o) => o.id === "scan") ? el("button", { type: "button", class: "btn secondary", "data-tool": "scan", title: TAGLINES.scan,
        onclick: (e) => { e.stopPropagation(); select("scan"); } }, icon("scan"), "Scan paper") : null));
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.target === drop && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); input.click(); } });
  drop.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.stopPropagation(); drop.classList.add("over"); } });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { drop.classList.remove("over"); if (hasFiles(e)) { e.preventDefault(); e.stopPropagation(); openFilesInReader(e.dataTransfer.files); } });
  const parts = [el("h1", { class: "sr-only" }, "Start"), drop, input];
  const pinned = toolsFrom(favTools());
  if (pinned.length) {
    parts.push(el("section", { class: "start-sec", "aria-label": "Pinned tools" }, el("span", { class: "lbl" }, "Pinned"),
      el("div", { class: "pin-row" }, ...pinned.map((op) => el("button", { type: "button", "data-tool": op.id, title: TAGLINES[op.id] || op.label,
        onclick: () => select(op.id) }, icon(op.id), op.label)))));
  }
  const recentList = el("div", { class: "recent-list" }, el("p", { class: "start-empty" }, "Looking for recent files…"));
  const multi = el("div", { class: "multi" },
    ...MULTI_TOOLS.filter(([id]) => ops.some((o) => o.id === id)).map(([id, line, name]) => {
      const op = ops.find((o) => o.id === id);
      return el("button", { type: "button", "data-tool": id, onclick: () => select(id) }, icon(id), el("span", {}, name || op.label, el("span", {}, line)));
    }),
    el("button", { type: "button", class: "more", "aria-haspopup": "dialog", "aria-controls": "flyout",
      onclick: (e) => { e.stopPropagation(); openFlyout("several", e.currentTarget); } }, "More for several files"));
  parts.push(el("div", { class: "start-cols" },
    el("section", { class: "start-sec", "aria-label": "Recent files" }, el("span", { class: "lbl" }, "Recent"), recentList),
    el("section", { class: "start-sec", "aria-label": "Start from several files" }, el("span", { class: "lbl" }, "Start from several files"), multi)));
  host.replaceChildren(...parts);
  host.hidden = false;
  await fillRecent(recentList, { emptyText: "PDFs you open show up here.", still: inHome });
}

// ---- Recent files: one list, on the start screen and under the reader's "Open a PDF" ----
const RECENT_ROWS = 10;
/** Recent files, newest first: those opened from this computer by path (the
 *  server's list, with each file's facts) and, in a browser, those opened
 *  with its Open dialog or dropped (by browser file handle: a name and a time). */
async function recentEntries() {
  let server = [], handles = [];
  try { ({ files: server } = await (await api("/api/recent")).json()); } catch (e) { server = []; }
  await filesReady;
  if (filesLib && filesLib.canOpen) handles = await filesLib.recentHandles().catch(() => []);
  return [
    ...(server || []).map((f) => ({ key: "path:" + f.path, name: f.name, title: f.path, facts: f.facts, opened: (f.opened || 0) * 1000,
      failed: !!f.failed, open: () => openInReaderById([f.id]), forget: () => jsonApi("/api/recent/forget", { path: f.path }),
      restore: (gone) => jsonApi("/api/recent/restore", { items: gone?.removed || [] }) })),
    ...handles.map((h) => ({ key: "handle:" + h.key, name: h.name, title: h.name, facts: null, opened: h.opened,
      open: async () => {
        const file = await filesLib.fileFromHandle(h.handle);  // (asks to read it again: from the click)
        if (!file) { appToast(`“${h.name}” can't be opened any more. It may have been moved or deleted.`, 6000); return; }
        filesLib.rememberHandle(h.handle);
        await select("reader");
        await reader.openFile(file);
      },
      forget: () => filesLib.forgetHandle(h.key), restore: (gone) => filesLib.restoreHandle(gone) })),
  ].sort((a, b) => b.opened - a.opened).slice(0, RECENT_ROWS);
}
/** Fill ``host`` with the Recent rows: the name, what ZPDF knows about the file
 *  (factsLine), when it was opened; a click opens it, × forgets it (everywhere:
 *  both places read the same lists). Resolves to the number of rows. */
/** The folder a path's file is in, by its name ("C:\\Work\\2024\\a.pdf" gives "2024"). */
function folderOf(path) {
  const parts = String(path).split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 2] : "";
}

async function fillRecent(host, { emptyText = "", onEmpty = null, still = () => true } = {}) {
  host.classList.add("recent-list");
  const entries = await recentEntries();
  if (!still()) return 0;
  const empty = () => { host.replaceChildren(...(emptyText ? [el("p", { class: "start-empty" }, emptyText)] : [])); onEmpty?.(); };
  if (!entries.length) { empty(); return 0; }
  // Two files of the same name are told apart by their folder.
  const names = {};
  for (const e of entries) names[e.name] = (names[e.name] || 0) + 1;
  // The folder note is worked out again when a row goes: the last file of a name needs none.
  const relabel = () => {
    const rows = [...host.querySelectorAll(".recent-row")];
    const count = {};
    for (const r of rows) count[r.dataset.name] = (count[r.dataset.name] || 0) + 1;
    for (const r of rows) {
      const facts = r.querySelector(".recent-facts");
      const text = [r.dataset.facts, count[r.dataset.name] > 1 && r.dataset.where && `in ${r.dataset.where}`].filter(Boolean).join(" · ");
      if (facts) { (facts.querySelector(".recent-facts-text") || facts).textContent = text; facts.hidden = !text; }
    }
  };
  host.replaceChildren(...entries.map((e) => {
    const folder = e.title !== e.name ? folderOf(e.title) : "";
    const where = names[e.name] > 1 ? folder : "";
    // (The reader couldn't open it last time, and it hasn't changed since: said in the error style.)
    const failed = e.failed && !e.facts?.for_people;  // (protected for people isn't a failure to open)
    const facts = failed ? "can't be opened" : factsLine(e.facts), bad = failed || facts === "can't be read";
    const line = [facts, where && `in ${where}`].filter(Boolean).join(" · ");
    const flag = !bad && !!(e.facts && e.facts.scanned && !e.facts.has_text);
    const row = el("div", { class: "recent-row", "data-key": e.key, "data-name": e.name, "data-facts": facts, "data-where": folder },
      el("button", { type: "button", class: "recent-open", title: e.title, onclick: () => e.open() },
        el("span", {}, el("span", { class: "recent-name" }, e.name), line ? el("span", { class: "recent-facts" + (flag ? " flag" : bad ? " bad" : "") }, bad ? icon("alert") : null, el("span", { class: "recent-facts-text" }, line)) : null),
        el("span", { class: "recent-when" }, whenText(e.opened))),
      el("button", { type: "button", class: "chip-btn recent-x", title: "Remove from Recent", "aria-label": `Remove ${e.name} from Recent`,
        onclick: async () => {
          let gone;
          try { gone = await e.forget(); } catch (err) { appToast("Couldn't remove it from Recent: " + (err.message || err)); return; }
          const next = row.nextElementSibling || row.previousElementSibling;
          row.remove();
          relabel();
          if (!host.querySelector(".recent-row")) empty(); else next?.querySelector(".recent-x")?.focus();
          appToast(`Removed ${e.name} from Recent`, 5000, ["Undo", async () => {
            try { await e.restore(gone); } catch (err) { appToast("Couldn't put it back: " + (err.message || err)); return; }
            if (!host.isConnected) return;
            await fillRecent(host, { emptyText, onEmpty, still });
            host.querySelector(`.recent-row[data-key="${CSS.escape(e.key)}"] .recent-open`)?.focus();
          }]);
        } }, icon("x")));
    return row;
  }));
  return entries.length;
}

// ---- page board (the page tools) ---------------------------------------------------
// With one PDF, the page tools show every page (see static/pages.js and the
// "board" entry of each tool). Dragging or clicking pages writes the page
// list into the tool's field, and typing in the field updates the previews.
let board = null, boardLib = null;
const BOARD_TEXT = {
  order: ["Drag pages into the new order", "Tip: click a page, then use the arrow keys to pick and Alt+arrow keys to move it. Delete removes a page, D duplicates it, Ctrl+Z undoes."],
  keep: ["Click the pages to extract", "Shift+click selects a range. With the keyboard: arrow keys, then Space to select; Ctrl+A selects all."],
  remove: ["Click the pages to delete", "Shift+click selects a range. With the keyboard: arrow keys, then Space to select; Ctrl+A selects all."],
  rotate: ["Click the pages to rotate", "The previews show the result. Shift+click selects a range; with no page selected, every page is rotated."],
  reverse: ["Preview of the result", ""],
  split: ["Click a page to start a new file there", "Each colour band becomes one file. With the keyboard: arrow keys, then Space."],
  insert: ["Click the pages to put a blank page after", "Shift+click selects a range; All puts a blank page after every page (for notes). With the keyboard: arrow keys, then Space."],
};
const boardField = () => current && current.board && current.board.field ? $("f-" + current.board.field) : null;
function boardChanged(pages) {
  const f = boardField();
  if (!f) return;
  f.value = current.board.mode === "split" ? splitsToRanges(pages, board.total) : boardLib.formatRanges(pages);
  // Pages clicked to split at: the split is now "at the pages I choose".
  const how = current.board.how && $("f-" + current.board.how);
  if (how && how.value !== "ranges") { how.value = "ranges"; updateVisibility(); }
}
// Split at bookmarks: show where each file starts (the bookmarks are read
// from the file once); by size, the files are only known when it runs.
const outlines = new WeakMap();  // File -> its bookmarks
async function showSplitBy(how) {
  const spec = current.board, file = board.file;
  if (how === "size") { board.setSplits([]); $("pages-hint").textContent = "The files are worked out when you click Run: each gets as many pages as fit in the size you choose."; return; }
  $("pages-hint").textContent = BOARD_TEXT.split[1];
  let list = outlines.get(file);
  if (!list) {
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      body.append("password", $("pw").value);
      body.append("detect", "");
      list = (await (await api("/api/outline", { method: "POST", body })).json()).items || [];
      outlines.set(file, list);
    } catch (e) { list = []; }
  }
  if (board.file !== file || current.board !== spec || $("f-" + spec.how)?.value !== "bookmarks") return;
  const level = Number($("f-level")?.value) || 1;
  board.setSplits([...new Set(list.filter((b) => b.level < level).map((b) => b.page - 1))].sort((a, b) => a - b));
}
// Split: pages that start a new file (0-based) <-> "1-3 4-7 8-12".
function splitsToRanges(starts, total) {
  const firsts = [0, ...starts], parts = [];
  firsts.forEach((a, i) => { const b = (firsts[i + 1] ?? total) - 1; parts.push(a === b ? `${a + 1}` : `${a + 1}-${b + 1}`); });
  return parts.join(" ");
}
function rangesToSplits(text, total) {
  const groups = text.split(/[;\s]+/).filter(Boolean).map((t) => boardLib.parseRanges(t, total));
  if (!groups.length || groups.some((g) => !g)) return null;
  const flat = groups.flat();
  if (flat.length !== total || flat.some((p, i) => p !== i)) return null;  // must cover every page once, in order
  return groups.slice(1).map((g) => g[0]);
}
async function syncBoard() {
  const spec = current && current.board;
  const active = !!spec && items.length > 0;
  $("pages-card").hidden = !active;
  if (!active || items.length > 1) {
    $("pages-sub").textContent = "";
    $("pages-hint").textContent = active ? "Page previews are shown for one file at a time. With several files, the pages typed under Options are used for each of them." : "";
    if (board) board.close();
    return;
  }
  const [sub, hint] = BOARD_TEXT[spec.effect || spec.mode];
  $("pages-sub").textContent = sub;
  $("pages-hint").textContent = hint;
  if (!boardLib) boardLib = await import("/static/pages.js");
  // (Loading it the first time takes a moment: the tool or its files may have changed meanwhile.)
  if (current?.board !== spec || items.length !== 1) return;
  if (!board) board = new boardLib.PageBoard($("page-board"), { onChange: boardChanged });
  if (board.mode !== spec.mode || board.effect !== (spec.effect || null) || board.tool !== current.id) {
    board.tool = current.id;
    board.configure({ mode: spec.mode, effect: spec.effect || null });
  }
  const it = items[0];
  if (!it) return;  // (the file was taken away while pages.js loaded)
  const result = await board.load(it.file, $("pw").value || null);
  if (result === "password" || result === "wrong-password") $("pw-box").open = true;
  if (result !== "ok" || board.file !== it.file || current.board !== spec) return;
  if (spec.degrees) board.setRotation($("f-" + spec.degrees).value);
  const f = boardField();
  if (!f) return;
  if (spec.mode === "split") {
    const how = spec.how && $("f-" + spec.how)?.value;
    if (how === "bookmarks" || how === "size") { await showSplitBy(how); return; }
    const every = Number($("f-" + spec.every)?.value) || 1;
    const byRanges = !spec.how || $("f-" + spec.how)?.value === "ranges";
    const typed = byRanges ? rangesToSplits(f.value, board.total) : null;
    board.setSplits(typed || [...Array(board.total).keys()].filter((p) => p > 0 && p % every === 0));
    f.value = splitsToRanges(board.splits, board.total);
    return;
  }
  const typed = boardLib.parseRanges(f.value, board.total);
  if (spec.mode === "order") {
    if (typed) board.setOrder(typed);
    else f.value = boardLib.formatRanges(board.order);
  } else board.setSelection(typed || []);
}
$("form").addEventListener("input", (e) => {
  const spec = current && current.board;
  if (!spec || !board || !board.doc || !boardLib || board.tool !== current.id) return;
  if (spec.degrees && e.target.id === "f-" + spec.degrees) board.setRotation(e.target.value);
  if (spec.how && (e.target.id === "f-" + spec.how || e.target.id === "f-level")) {
    const how = $("f-" + spec.how).value;
    if (how === "bookmarks" || how === "size") { showSplitBy(how); return; }
    $("pages-hint").textContent = BOARD_TEXT.split[1];
    // From the bookmarks to "at the pages I choose": start from where they split.
    if (how === "ranges" && e.target.id === "f-" + spec.how) { boardField().value = splitsToRanges(board.splits, board.total); return; }
  }
  if (spec.how && e.target.id === "f-" + spec.how && e.target.value === "every") {
    const every = Math.max(1, Number($("f-" + spec.every).value) || 1);  // back to every few pages
    board.setSplits([...Array(board.total).keys()].filter((p) => p > 0 && p % every === 0));
    boardField().value = splitsToRanges(board.splits, board.total);
    return;
  }
  if (spec.every && e.target.id === "f-" + spec.every) {
    const every = Math.max(1, Number(e.target.value) || 1);
    board.setSplits([...Array(board.total).keys()].filter((p) => p > 0 && p % every === 0));
    boardField().value = splitsToRanges(board.splits, board.total);
    return;
  }
  if (spec.mode === "split" && e.target.id === "f-" + spec.field) {
    const starts = rangesToSplits(e.target.value, board.total);
    if (starts) board.setSplits(starts);
    return;
  }
  if (e.target.id !== "f-" + spec.field) return;
  const pages = boardLib.parseRanges(e.target.value, board.total);
  if (spec.mode === "order") { if (pages) board.setOrder(pages); }
  else if (pages || !e.target.value.trim()) board.setSelection(pages || []);
});

// ---- tool panels ---------------------------------------------------------------------
// Tools with a "panel" get an extra card between Files and Options (e.g. the
// bookmark editor). Each panel module is loaded the first time it's needed.
const panels = {};
const PANELS = {
  bookmarks: {
    title: "Bookmarks", sub: "Shown in the sidebar of PDF readers",
    async setup(host) {
      const { BookmarkEditor } = await import("/static/bookmarks.js");
      const editor = new BookmarkEditor(host, {
        load: async (detect) => {
          const body = new FormData();
          body.append("file", items[0].file, items[0].file.name);
          body.append("password", $("pw").value);
          body.append("detect", detect ? "1" : "");
          return (await api("/api/outline", { method: "POST", body })).json();
        },
        onChange: (list) => { const f = $("f-outline"); if (f) f.value = JSON.stringify(list); },
      });
      return {
        async sync() {
          const one = items.length === 1;
          $("panel-hint").textContent = one ? "Indent a bookmark to put it under the one above it."
            : "Bookmarks can be edited for one file at a time. With several files, choose “Create from headings” under Options.";
          host.hidden = !one;
          if (!one) { editor.reset(); return; }
          await editor.open(items[0].id + "\u0000" + $("pw").value);
          editor.onChange(editor.items);  // the form is rebuilt when switching tools
          muteBookmarks();
        },
      };
    },
  },
};
PANELS.crop = {
  title: "Crop area", sub: "Drag the edges to choose what to keep",
  async setup(host) {
    const { RegionEditor } = await import("/static/regions.js");
    const sides = ["top", "right", "bottom", "left"];
    const editor = new RegionEditor(host, { mode: "crop", onChange: (m) => {
      for (const k of sides) { const f = $("f-" + k); if (f) f.value = Math.max(0, (m[k] * 25.4) / 72).toFixed(1); }
    } });
    const fromFields = () => Object.fromEntries(sides.map((k) => [k, Math.max(0, Number($("f-" + k)?.value) || 0) * 72 / 25.4]));
    $("form").addEventListener("input", (e) => { if (current.panel === "crop" && sides.includes(e.target.name)) editor.setCrop(fromFields()); });
    return {
      async sync() {
        const one = items.length === 1;
        host.hidden = !one;
        $("panel-hint").textContent = one ? "The darkened part is trimmed off. The same margins are used for every page (or the pages you list under Options)."
          : "The crop area can be drawn for one file at a time. With several files, type the margins under Options.";
        if (!one) { editor.close(); return; }
        editor.setCrop(fromFields());
        const result = await editor.load(items[0].file, $("pw").value || null);
        if (result === "password" || result === "wrong-password") $("pw-box").open = true;
      },
    };
  },
};
PANELS.redact = {
  title: "Pages", sub: "Drag on a page to black out an area",
  async setup(host) {
    const { RegionEditor } = await import("/static/regions.js");
    // The button says what happens: "Black out 3 matches" once they're found (or "Black out 5 areas" with boxes drawn).
    const sayCount = () => {
      if (current.panel !== "redact") return;
      const found = items.length === 1 ? Object.values(editor.matches || {}).reduce((n, l) => n + l.length, 0) : 0;
      const drawn = items.length === 1 ? editor.boxCount : 0, n = found + drawn;
      $("run-label").textContent = !n ? current.action : `${current.action} ${n} ${drawn ? (n === 1 ? "area" : "areas") : (n === 1 ? "match" : "matches")}`;
    };
    const editor = new RegionEditor(host, { mode: "redact", onChange: (boxes) => { const f = $("f-boxes"); if (f) f.value = JSON.stringify(boxes); sayCount(); updateSummary(); } });
    let timer = null, asked = 0;
    const findMatches = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (current.panel !== "redact" || items.length !== 1 || !editor.doc) return;
        const ask = ++asked, body = new FormData();
        body.append("file", items[0].file, items[0].file.name);
        body.append("password", $("pw").value);
        for (const k of ["terms", "regex", "emails", "phones", "numbers", "ssn", "ein", "uk_ni", "iban", "credit_card", "case_sensitive", "whole_words"]) {
          const f = $("f-" + k);
          if (f) body.append(k, f.type === "checkbox" ? String(f.checked) : f.value);
        }
        try {
          const data = await (await api("/api/redact/find", { method: "POST", body })).json();
          if (ask === asked) { editor.setMatches(data.boxes); sayCount(); }
        } catch (e) { /* the preview is optional */ }
      }, 450);
    };
    $("form").addEventListener("input", (e) => { if (current.panel === "redact" && e.target.name !== "boxes") findMatches(); });
    $("form").addEventListener("change", (e) => { if (current.panel === "redact" && e.target.type === "checkbox") findMatches(); });
    return {
      async sync() {
        const one = items.length === 1;
        host.hidden = !one;
        $("panel-hint").textContent = one ? "Striped: found by the words and numbers under Options. Black: boxes you drew. Click a box to move, resize or remove it. What’s under the boxes is removed for good."
          : "Boxes can be drawn for one file at a time. With several files, the words and numbers under Options are blacked out in each.";
        if (!one) { editor.close(); return; }
        const result = await editor.load(items[0].file, $("pw").value || null);
        if (result === "password" || result === "wrong-password") $("pw-box").open = true;
        if (result !== "ok") return;
        const f = $("f-boxes");
        if (f && f.value) editor.setBoxes(JSON.parse(f.value));
        else if (f) f.value = JSON.stringify(Object.fromEntries(Object.entries(editor.boxes).filter(([, l]) => l.length).map(([p, l]) => [Number(p) + 1, l])));
        findMatches();
      },
    };
  },
};
const fileKey = (file) => `${file.name}|${file.size}|${file.lastModified}`;
PANELS.form = {
  title: "Pages", sub: "Drag or click where people should type, tick or sign",
  async setup(host) {
    const { RegionEditor } = await import("/static/regions.js");
    const editor = new RegionEditor(host, {
      mode: "form", onChange: (fields, edits) => {
        const f = $("f-fields"), e = $("f-edits");
        if (f) f.value = JSON.stringify(fields);
        if (e && edits) e.value = edits.length ? JSON.stringify(edits) : "";
      },
      async detect() {
        const body = new FormData();
        body.append("file", items[0].file, items[0].file.name);
        body.append("password", $("pw").value);
        return (await (await api("/api/form/detect", { method: "POST", body })).json()).fields;
      },
    });
    return {
      async sync() {
        host.hidden = !items.length;
        $("panel-hint").textContent = items.length > 1
          ? "The fields drawn on the first file are added to each file (useful for copies of the same blank form)."
          : "Find fields adds the lines, boxes and tick boxes of a printed form. Or choose a field type, then drag a box on the page — or just click for a box of the usual size. Radio buttons added one after another are one group. Click a field to move, resize, rename or remove it. Striped: fields already in the file, which can be changed the same way.";
        if (!items.length) { editor.close(); return; }
        const result = await editor.load(items[0].file, $("pw").value || null);
        if (result === "password" || result === "wrong-password") $("pw-box").open = true;
        if (result !== "ok") return;
        const f = $("f-fields");
        if (f && f.value) editor.setBoxes(JSON.parse(f.value));
        else editor.onChange({});
        // The file's own fields can be changed when there's one file: with
        // several, what's drawn goes on each, and their fields differ.
        const one = items.length === 1, saved = $("f-edits");
        let edits = null;
        try { edits = one && saved && saved.value && saved.dataset.file === fileKey(items[0].file) ? JSON.parse(saved.value) : null; } catch (e) { edits = null; }
        if (saved) { saved.dataset.file = fileKey(items[0].file); if (!edits) saved.value = ""; }
        try {
          const body = new FormData();
          body.append("file", items[0].file, items[0].file.name);
          body.append("password", $("pw").value);
          body.append("details", "1");
          editor.setExisting((await (await api("/api/form/fields", { method: "POST", body })).json()).fields, one, edits);
        } catch (e) { /* showing them is optional */ }
      },
    };
  },
};
PANELS.recipe = {
  title: "Recipe", sub: "Runs from top to bottom", always: true,
  async setup(host) {
    const { RecipeBuilder } = await import("/static/recipes.js");
    const meta = await (await api("/api/recipes")).json();
    const usable = meta.step_ops.map((id) => ops.find((o) => o.id === id)).filter(Boolean);
    const json = (method, path, data) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }).then((r) => r.json());
    const builder = new RecipeBuilder(host, {
      ops: usable,
      api: { list: async () => meta.recipes ? { recipes: meta.recipes } : (await api("/api/recipes")).json(),
             save: (name, steps) => json("POST", "/api/recipes/save", { name, steps }),
             remove: (name) => json("POST", "/api/recipes/delete", { name }) },
      onChange: (steps, name) => {
        const s = $("f-steps"), n = $("f-recipe_name");
        if (s) s.value = JSON.stringify(steps);
        if (n) n.value = name || "";
      },
    });
    await builder.refresh();
    meta.recipes = null;  // later refreshes ask the server
    return {
      async sync() {
        $("panel-hint").textContent = items.length > 1 ? "The recipe runs on each file; the results come back as a ZIP." : "";
        builder.emit();
      },
    };
  },
};
function muteBookmarks() {
  const mode = $("f-mode");
  $("panel-card").classList.toggle("muted", !!mode && current.panel === "bookmarks" && mode.value !== "edit");
}
async function syncPanel() {
  const kind = current && current.panel;
  const card = $("panel-card");
  card.hidden = !kind || (!items.length && !PANELS[kind].always);
  card.classList.remove("muted");
  for (const [k, p] of Object.entries(panels)) p.host.hidden = k !== kind;
  if (card.hidden) return;
  $("panel-title").textContent = PANELS[kind].title;
  $("panel-sub").textContent = PANELS[kind].sub || "";
  if (!panels[kind]) {
    const host = el("div");
    $("panel-host").append(host);
    panels[kind] = { host, loading: PANELS[kind].setup(host) };
  }
  const panel = await panels[kind].loading;
  if (current.panel === kind) await panel.sync();
}
$("form").addEventListener("input", (e) => { if (e.target.id === "f-mode") muteBookmarks(); });

// ---- options -----------------------------------------------------------------
const PATH_FIELDS = new Set(["directory", "dest"]);
function renderForm() { renderFields(current, $("form"), "f-", "No options — add files and run."); }
/** A tool's options as fields in ``form``, each with the id ``prefix`` + its
 *  key: the tool page's form ("f-"), or a tool opened in the file panel. */
function renderFields(op, form, prefix, empty) {
  form.replaceChildren();
  if (!op.fields.some((f) => f.kind !== "hidden")) form.append(el("p", { class: "empty-note" }, empty));
  for (const f of op.fields) {
    const id = prefix + f.key;
    let input;
    if (f.kind === "hidden") { form.append(el("input", { id, name: f.key, type: "hidden", value: f.default ?? "" })); continue; }
    if (f.kind === "select") {
      input = el("select", { id, name: f.key });
      for (const [v, label] of f.choices) input.append(el("option", { value: v, selected: String(f.default) === v }, label));
      // A long choice is cut short in a narrow box: its whole text shows on hover.
      const whole = () => { input.title = input.selectedOptions[0]?.textContent || ""; };
      input.addEventListener("change", whole);
      whole();
    } else if (f.kind === "range") {
      // Limits first, then the value: set the other way, the browser rounds it to the default step.
      input = el("input", { id, name: f.key, type: "range", min: f.min, max: f.max, step: f.step || "any" });
      input.value = f.default ?? "";
    } else if (f.kind === "check") {
      input = el("input", { id, name: f.key, type: "checkbox", checked: !!f.default });
    } else if (f.kind === "file") {
      input = el("input", { id, name: f.key, type: "file", accept: f.accept || "", multiple: !!f.multiple });
    } else {
      input = el("input", { id, name: f.key, type: f.kind === "number" ? "number" : f.kind === "password" ? "password" : "text",
        ...(f.kind === "password" ? { autocomplete: "off" } : {}),
        value: f.default ?? "", placeholder: f.placeholder || "", min: f.min, max: f.max,
        step: f.step || (f.kind === "number" ? "any" : null), required: !!f.required, spellcheck: "false" });
    }
    let control = input;
    if (f.kind === "range") {  // a slider, its value beside it
      const shown = el("output", { for: id }, fmtRange(f, input.value));
      input.addEventListener("input", () => { shown.textContent = fmtRange(f, input.value); });
      control = el("div", { class: "range" }, input, shown);
    }
    if (PATH_FIELDS.has(f.key) && isDesktop()) {
      control = el("div", { class: "with-btn" }, input, el("button", { class: "btn secondary", type: "button", onclick: async () => {
        const chosen = await window.pywebview.api.pick_folder();
        if (chosen) { input.value = chosen; input.dispatchEvent(new Event("input", { bubbles: true })); }
      } }, "Browse…"));
    }
    const wide = PATH_FIELDS.has(f.key) || (f.hint && f.hint.length > 60);
    const wrap = f.kind === "check"
      ? el("div", { class: "field check" + (wide ? " wide" : ""), "data-key": f.key }, input, el("label", { for: id }, f.label))
      : el("div", { class: "field" + (wide ? " wide" : ""), "data-key": f.key }, el("label", { for: id }, f.label), control);
    if (f.strength) {
      // How hard the password is to guess, said as it's typed (worked out by the app on this computer).
      const bar = el("div", { class: "pw-meter", "data-score": "", role: "status", "aria-live": "polite" },
        el("span", { class: "pw-bars", "aria-hidden": "true" }, el("i"), el("i"), el("i"), el("i"), el("i")), el("span", { class: "pw-words" }));
      let seq = 0, timer = 0;
      const rate = async () => {
        const mine = ++seq;
        if (!input.value) { bar.dataset.score = ""; bar.lastChild.textContent = ""; return; }
        try {
          const got = await (await api("/api/password/strength", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: input.value }) })).json();
          if (mine !== seq) return;
          bar.dataset.score = String(got.score);
          bar.lastChild.textContent = got.label ? `${got.label}. ${got.advice}` : "";
        } catch (e) { /* the meter is a help, not a gate */ }
      };
      input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(rate, 200); });
      wrap.append(bar);
    }
    if (f.hint) wrap.append(el("div", { class: "hint" }, f.hint));
    form.append(wrap);
  }
  updateVisibility(op, form, prefix);
}
$("form").addEventListener("input", () => updateVisibility());
$("form").addEventListener("input", () => { if (current?.url_field) updateSummary(); });
function fmtRange(f, v) {
  const n = Number(v);
  return f.unit === "%" ? Math.round(n * 100) + "%" : f.unit ? `${Math.round(n)}${f.unit}` : String(v);
}

// ---- live preview: page 1 of the result, redrawn as the options change -----------
let liveSeq = 0, liveTimer = 0, liveUrl = null;
function liveWanted() { return !!(current && current.live && items.length && isPdf(items[0].file)); }
function scheduleLive(delay = 350) {
  clearTimeout(liveTimer);
  const on = liveWanted();
  $("live").hidden = !on;
  $("options-card").classList.toggle("has-live", on);
  if (!on) { liveSeq++; return; }
  $("live").classList.add("busy");
  liveTimer = setTimeout(updateLive, delay);
}
async function updateLive() {
  const seq = ++liveSeq, op = current.id, file = items[0].file;
  const { opts } = collectOptions();
  const body = new FormData();
  body.append("op", op);
  body.append("password", $("pw").value);
  for (const [k, v] of Object.entries(opts)) body.append(k, v);
  body.append("file", file, file.name);
  let message = "";
  try {
    const blob = await (await api("/api/preview", { method: "POST", body })).blob();
    if (seq !== liveSeq) return;
    if (liveUrl) URL.revokeObjectURL(liveUrl);
    liveUrl = URL.createObjectURL(blob);
    $("live-img").src = liveUrl;
  } catch (e) {
    if (seq !== liveSeq) return;
    message = String(e.message || e).replace(/^(?!\S+\.[A-Za-z0-9]{2,5}(?:[\s:]|$))./, (c) => c.toUpperCase());
  }
  $("live").classList.remove("busy");
  $("live").classList.toggle("failed", !!message);
  $("live-msg").hidden = !message;
  $("live-msg").textContent = message ? `No preview: ${message}` : "";
  $("live-cap").textContent = (op === "nup" || op === "booklet" ? "The first sheet" : "Page 1") + " as it will come out"
    + (items.length > 1 ? ` (${file.name})` : "");
}
$("form").addEventListener("input", () => scheduleLive());
$("form").addEventListener("change", () => scheduleLive());
$("pw").addEventListener("change", () => scheduleLive(0));
// A click shows it larger (small print such as page numbers); any click or Esc closes it.
$("live-img").title = "Click to see it larger";
$("live-img").addEventListener("click", () => { if (!liveUrl) return; $("live-big-img").src = liveUrl; $("live-big").showModal(); });
$("live-big").addEventListener("click", () => $("live-big").close());
$("form").addEventListener("submit", (e) => { e.preventDefault(); run(false); });
function updateVisibility(op = current, form = $("form"), prefix = "f-") {
  for (const f of op.fields) {
    if (!f.show_if) continue;
    // (In the form, which may not be on the page yet.)
    const ok = Object.entries(f.show_if).every(([k, v]) => form.querySelector("#" + CSS.escape(prefix + k))?.value === v);
    form.querySelector(`.field[data-key="${f.key}"]`).hidden = !ok;
  }
}
function collectOptions(op = current, prefix = "f-") {
  const opts = {}, extra = {};
  for (const f of op.fields) {
    const input = $(prefix + f.key);
    if (f.kind === "check") opts[f.key] = input.checked ? "true" : "false";
    else if (f.kind === "file") { if (input.files[0]) extra[f.key] = f.multiple ? [...input.files] : input.files[0]; }
    else opts[f.key] = input.value;
  }
  return { opts, extra };
}
/** The first required field left empty, if any. */
function firstMissing(op, prefix) { return op.fields.find((f) => f.required && !$(prefix + f.key).value.trim()); }
function missingField() {
  const f = firstMissing(current, "f-");
  if (f) foldForm(null);  // (a folded form opens to show the field)
  if (f) { showError(`Please fill in “${f.label}”.`); fieldError($("f-" + f.key), `Fill in “${f.label}” to go on.`); $("f-" + f.key).focus(); }
  return !!f;
}

// ---- running -------------------------------------------------------------------
async function run(preview) {
  clearMessages();
  if (current.files === null) return runFolder(!preview);
  const need = current.files === "pdf2" ? 2 : current.min || 1;
  const typedAddress = current.url_field && ($("f-" + current.url_field)?.value || "").trim();
  const bad = unreadable(), use = items.filter((it) => !bad.includes(it));
  if (bad.length && use.length < need && !typedAddress) {
    const say = bad.length === 1 ? plainError(`${bad[0].file.name} is not a readable PDF (${bad[0].error})`)
      : `${bad.length} files can't be opened — they may be damaged. Try Repair, or remove them.`;
    showError(say, { detail: bad.map((it) => `${it.file.name}: ${String(it.error).replace(/^.* is not a readable PDF \((.*)\)$/s, "$1")}`).join("\n"), focus: true });
    return;
  }
  if (items.length < need && !typedAddress) { showError(need === 1 ? (current.url_field ? "Type a web address, or add a saved web page." : "Add a file first.") : `Add ${current.files === "pdf2" ? "exactly 2" : "at least " + need} files.`); return; }
  if (missingField()) return;
  const { opts, extra } = collectOptions();
  const body = new FormData();
  body.append("op", current.id);
  body.append("password", $("pw").value);
  for (const [k, v] of Object.entries(opts)) body.append(k, v);
  for (const [k, v] of Object.entries(extra)) for (const file of [v].flat()) body.append(k, file, file.name);
  for (const it of use) body.append("files", it.file, it.file.name);  // (files that can't be opened are left out, as the bar says)
  busy(true, current.id === "ocr" ? "Recognising text — this can take a while…" : "Working…");
  try {
    const res = await runJob("/api/run", { method: "POST", body, headers: isDesktop() ? { "X-Pdfm-Keep": "1" } : {} });
    if (!res) return;  // cancelled
    const name = decodeURIComponent(res.headers.get("X-Pdfm-Filename") || "") || "result.pdf";
    const resultId = res.headers.get("X-Pdfm-Result-Id");
    const note = decodeURIComponent(res.headers.get("X-Pdfm-Note") || "");
    const blob = await res.blob();
    await showResult(name, blob, resultId, note, bad.map((it) => it.file.name));
  } catch (err) {
    const said = plainError(err.message);
    let action = null;
    if (current.id === "translate" && /language pack/i.test(String(err.message || ""))) {
      const needed = [...new Set([opts.source_lang, opts.target_lang].filter((c) => c && c !== "en"))];
      if (needed.length) {
        const downloadAndTranslate = async () => {
          clearMessages();
          busy(true, `Downloading language pack (${needed.join(", ")})…`);
          try {
            for (const code of needed) {
              const dlRes = await runJob("/api/i18n/pack/download", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ code }),
              });
              if (!dlRes) {
                busy(false);
                return;
              }
            }
          } catch (dlErr) {
            busy(false);
            showError(dlErr.message || String(dlErr), { focus: true });
            return;
          }
          busy(false);
          await run(preview);
        };
        action = { label: `Download pack (${needed.join(", ")}) & translate`, onClick: downloadAndTranslate };
      }
    }
    showError(said, { detail: err.message, focus: true, action });
    // …and by the button too, where the eyes are.
    const runErrNodes = [icon("alert"), String(said || "").replace(/^(?!\S+\.[A-Za-z0-9]{2,5}(?:[\s:]|$))./, (c) => c.toUpperCase())];
    if (action) {
      runErrNodes.push(" ", el("button", { type: "button", class: "btn seal sm", onclick: action.onClick }, icon("download"), action.label));
    }
    $("run-err").replaceChildren(...runErrNodes);
    $("run-err").hidden = false;
  } finally { busy(false); }
}

async function showResult(name, blob, resultId, note = "", skipped = []) {
  if (lastUrl) URL.revokeObjectURL(lastUrl);
  lastUrl = URL.createObjectURL(blob);
  const before = items.reduce((n, it) => n + it.file.size, 0);
  let sub = humanSize(blob.size);
  if (current.id === "compress") {
    const pct = (1 - blob.size / before) * 100;
    sub = `${humanSize(before)} → ${humanSize(blob.size)} · ` + (pct <= 0 ? "already compact" : (pct > 99 ? pct.toFixed(1) : Math.round(pct)) + "% smaller");
  }
  const thumb = el("div", { class: "thumb" }, icon(/\.zip$/i.test(name) ? "zip" : "file"));
  const subEl = el("div", { class: "result-sub" }, sub);
  const actions = el("div", { class: "result-actions" },
    ...saveControls(blob, name, resultId, lastUrl, (...said) => subEl.replaceChildren(...said)));
  if (/\.pdf$/i.test(name)) {
    actions.append(el("button", { class: "btn secondary", type: "button", onclick: () => openInReader(new File([blob], name, { type: "application/pdf" })) }, icon("reader"), "Open in reader"));
  }
  actions.append(el("button", { class: "btn ghost", type: "button", title: "Clear the files to start again with this tool", onclick: startOver }, "Start over"));
  $("result").classList.remove("info");
  $("result").replaceChildren(thumb,
    el("div", { class: "result-body" },
      el("div", { class: "result-title" }, icon("check"), "Done"),
      el("div", { class: "result-name" }, name), subEl,
      note ? el("div", { class: "result-note" }, ...note.split("\n").map((line) => el("div", /^Careful\b/.test(line) ? { class: "careful" } : {}, line))) : null,
      // Files that couldn't be opened were left out: said again here, by name.
      skipped.length ? el("div", { class: "result-skipped" }, icon("alert"),
        el("span", {}, `${plural(skipped.length, "file")} ${skipped.length === 1 ? "was" : "were"} left out (can't be opened): `, el("b", {}, skipped.join(", ")))) : null,
      actions));
  $("result").hidden = false;
  updateSummary();
  $("result").scrollIntoView({ behavior: smoothScroll(), block: "nearest" });
  const auto = actions.querySelector("a[data-auto]");
  if (auto) auto.click();  // browsers without a Save dialog: download straight away
  if (/\.pdf$/i.test(name)) {
    const body = new FormData();
    body.append("file", blob, name);
    body.append("password", $("pw").value);
    try {
      const info = await (await api("/api/inspect", { method: "POST", body })).json();
      if (info.thumb) thumb.replaceChildren(el("img", { src: info.thumb, alt: "Preview of the result" }));
      if (info.pages != null) subEl.prepend(plural(info.pages, "page") + " · ");
    } catch (e) { /* preview is optional */ }
  }
}

/** The buttons that save a tool's result, the way this window can: the
 *  desktop app's Save dialog, the browser's own (Edge, Chrome), or a
 *  download. ``said(...)`` shows where it went; ``download`` names the plain
 *  download link (the tool page clicks it by itself, data-auto). */
function saveControls(blob, name, resultId, href, said, { onError = showError, download = "Download" } = {}) {
  if (isDesktop() && resultId) {
    let reveal = null;
    const save = el("button", { class: "btn seal", type: "button" }, icon("download"), "Save…");
    save.addEventListener("click", async () => {
      try {
        const path = await window.pywebview.api.save_result(resultId, name);
        if (!path) return;
        said(el("span", { class: "saving" }, "Saved"), ` to ${path}`);
        appToast(`Saved to ${path}`);
        if (!reveal) save.after(reveal = el("button", { class: "btn secondary", type: "button", "data-reveal": "" }, icon("folder"), "Show in folder"));
        reveal.onclick = () => window.pywebview.api.reveal(path);
      } catch (e) { onError(String(e.message || e)); }
    });
    return [save];
  }
  if (filesLib && filesLib.canSave) {
    // Edge or Chrome: the computer's own Save dialog (it needs a click).
    const save = el("button", { class: "btn seal", type: "button" }, icon("download"), "Save…");
    save.addEventListener("click", async () => {
      try {
        const saved = await filesLib.saveAs(blob, name);
        if (saved) { said(el("span", { class: "saving" }, "Saved"), ` as ${saved}`); appToast(`Saved as ${saved}`); }
      } catch (e) { onError("Could not save: " + (e.message || e)); }
    });
    return [save, el("a", { class: "btn secondary", href, download: name }, "Download")];
  }
  return [el("a", { class: "btn", href, download: name, "data-auto": "" }, icon("download"), download)];
}

async function runFolder(apply) {
  if (missingField()) return;
  const { opts } = collectOptions();
  if (apply && (opts.move === "true" || opts.delete === "true" || current.id === "rename")) {
    const [title, label] = current.id === "rename" ? ["Rename the files?", "Rename files"]
      : opts.delete === "true" ? ["Delete the duplicate files?", "Delete duplicates"] : ["Move the files?", "Move files"];
    if (await appAsk(title, "This changes the files on your computer, as the preview shows.",
      [["cancel", "Cancel"], ["go", label, true]]) !== "go") return;
  }
  busy(true, apply ? "Applying…" : "Previewing…", apply ? "run" : "preview");
  try {
    const res = await runJob("/api/folder", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op: current.id, options: opts, apply }) });
    if (!res) return;  // cancelled
    const data = await res.json();
    if (data.matches) { showSearchResults(data, opts.query); return; }
    const n = data.rows.length;
    const msg = n === 0 ? "Nothing to do — everything is already in order."
      : apply ? `${plural(n, "file")} handled.` : `${plural(n, "file")} would change. Nothing has changed yet — click Apply to do it.`;
    $("result").classList.toggle("info", !apply);  // a preview has changed nothing yet: not the green banner
    $("result").replaceChildren(el("div", { class: "result-body" }, el("div", { class: "result-title" }, icon(apply ? "check" : "alert"), apply ? "Done" : "Preview"), el("div", { class: "result-sub" }, msg)));
    $("result").hidden = false;
    if (n) {
      const table = el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "File"), el("th", {}, apply ? "Result" : "Will become"))));
      const tbody = el("tbody");
      for (const [a, b] of data.rows) tbody.append(el("tr", {}, el("td", {}, a), el("td", {}, b)));
      table.append(tbody);
      $("result-table").replaceChildren(table);
      $("result-table").hidden = false;
    }
  } catch (err) { showError(err.message); } finally { busy(false); }
}

function showSearchResults(data, query) {
  const pages = data.matches.length, files = new Set(data.matches.map((m) => m.path)).size;
  const best = data.how === "best";
  let msg = pages ? `Found on ${plural(pages, "page")} in ${plural(files, "file")} (${plural(data.files, "PDF")} searched)${data.limited ? " — showing the first " + pages : ""}.`
    : `Not found in the ${plural(data.files, "PDF")} searched.`;
  if (best && pages) msg = `${pages === 1 ? "The best match" : `The ${pages} best matches`} in ${plural(data.files, "PDF")}, the closest first.${data.limited ? " There are more; a longer question narrows them." : ""}`
    + (data.meaning ? "" : /[\u2e80-\u9fff\uac00-\ud7af]/.test(query) ? " Matched by its words: questions are understood in English only." : " Matched exactly, as a number or a code is.");
  else if (best) msg = `Nothing like that in the ${plural(data.files, "PDF")} searched.`;
  const extra = data.skipped.length ? ` ${plural(data.skipped.length, "file")} couldn't be searched (password protected or unreadable).` : "";
  const said = el("div", { class: "result-sub", id: "search-summary", tabindex: "-1" }, msg + extra);
  // Results come up under the title: the form folds into one line (with Edit) once there's something to show.
  foldForm(pages ? `“${query}” in ${$("f-directory")?.value || "the folder"}` : null);
  // What was found is an answer, not a job done: the plain banner, not the green one.
  $("result").classList.add("info");
  $("result").replaceChildren(el("div", { class: "result-body" },
    el("div", { class: "result-title" }, icon("search"), "Search"), said));
  $("result").hidden = false;
  // Focus on what was found (it's read out), in sight above the action bar.
  requestAnimationFrame(() => { said.focus({ preventScroll: true }); said.scrollIntoView({ block: "nearest", behavior: smoothScroll() }); });
  if (!pages) return;
  // One group per folder (its path once, as a heading), in the order the results come.
  const folders = new Map();
  for (const m of data.matches) {
    const name = m.path.split(/[\\/]/).pop(), folder = m.path.slice(0, m.path.length - name.length - 1);
    if (!folders.has(folder)) folders.set(folder, []);
    folders.get(folder).push([m, name]);
  }
  const groups = [], root = $("f-directory")?.value || "", told = new Set();
  for (const [folder, rows] of folders) {
    const tbody = el("tbody", { class: "search-group" },
      el("tr", { class: "search-folder-row" }, el("th", { colspan: "3", scope: "colgroup", class: "search-folder", title: folder || "/" },
        icon("folder"), relativeFolder(folder, root))));
    for (const [m, name] of rows) {
      // A copy elsewhere: named once per file (on its first row), by the other copy's name, and its folder when the names match.
      const also = (m.also || []).length && !told.has(m.path) ? sameAsText(m, name, root) : "";
      told.add(m.path);
      const open = () => openInReaderById([m.id], { page: m.page, find: best ? m.find : query });
      // Only the words that matched are marked: the phrase found, or (best matches) the question's own words in the snippet.
      const text = best ? markWords(m.before + m.match + m.after, query) : [m.before, el("mark", {}, m.match), m.after];
      tbody.append(el("tr", { class: "search-row", tabindex: "0", role: "button", "aria-label": `Open page ${m.page} of ${name}`, title: "Open this page in the reader",
        onclick: open, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } } },
        el("td", {}, el("strong", { class: "search-name" }, name),
          also ? el("div", { class: "search-also", title: m.also.join("\n") }, also) : null),
        el("td", { class: "search-page" }, `p. ${m.page}`),
        el("td", {}, ...text, m.count > 1 && !best ? el("span", { class: "search-more" }, ` +${m.count - 1} more`) : null)));
    }
    groups.push(tbody);
  }
  $("result-table").replaceChildren(el("table", { class: "search-results" },
    el("thead", {}, el("tr", {}, el("th", {}, "File"), el("th", {}, "Page"), el("th", {}, "Text"))), ...groups));
  $("result-table").hidden = false;
}

/** A folder under the searched one, as the part below it: "Searched folder"
 *  for the folder itself, "Accounts\\2024" below it (the full path is the
 *  heading's tooltip). Elsewhere: the full path. */
function relativeFolder(folder, root) {
  const trim = (p) => String(p || "").replace(/[\\/]+$/, "");
  const f = trim(folder), r = trim(root);
  if (!r) return f || "/";
  const same = (a, b) => (/^[a-z]:|\\/i.test(r) ? a.toLowerCase() === b.toLowerCase() : a === b);  // (Windows paths ignore case)
  if (same(f, r)) return "Searched folder";
  if (f.length > r.length && /[\\/]/.test(f[r.length]) && same(f.slice(0, r.length), r)) return f.slice(r.length + 1);
  return f || "/";
}
/** "The same as Invoice.pdf in Backup": the other copy, named so it's told apart from this one. */
function sameAsText(m, name, root) {
  const other = m.also[0], otherName = other.split(/[\\/]/).pop();
  const where = otherName === name ? ` in ${relativeFolder(other.slice(0, other.length - otherName.length - 1), root)}` : "";
  return `The same as ${otherName}${where}${m.also.length > 1 ? " and " + plural(m.also.length - 1, "other") : ""}`;
}

/** ``text`` with the words of ``query`` marked (and words that start with
 *  them: "owe" marks "owed"); little words such as "how" and "do" aren't. */
const QUIET_WORDS = new Set("a an and are as at be but by can could did do does for from had has have how i if in into is it its me my much no not of on or our so than that the their them then there these they this to us was we were what when where which who whom why will with would you your".split(" "));
function markWords(text, query) {
  const words = [...new Set(String(query).toLowerCase().match(/[\p{L}\p{N}]+/gu) || [])].filter((w) => w.length > 1 && !QUIET_WORDS.has(w));
  if (!words.length) return [text];
  const esc = (w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${words.sort((a, b) => b.length - a.length).map(esc).join("|")})[\\p{L}\\p{N}]*`, "giu");
  const parts = [];
  let at = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > at) parts.push(text.slice(at, m.index));
    parts.push(el("mark", {}, m[0]));
    at = m.index + m[0].length;
  }
  if (at < text.length) parts.push(text.slice(at));
  return parts;
}
/** A folder tool's form, folded to one line ("“query” in folder · Edit") above
 *  its results; null opens it again. */
function foldForm(line) {
  const recap = $("form-recap");
  document.body.classList.toggle("form-folded", !!line);
  recap.hidden = !line;
  if (!line) return;
  const edit = el("button", { type: "button", class: "link-btn", "aria-controls": "options-card", "aria-expanded": "false" }, "Edit");
  edit.addEventListener("click", () => { foldForm(null); $("f-query")?.focus(); });
  recap.replaceChildren(icon("search"), el("span", { class: "recap-text" }, line), el("span", { "aria-hidden": "true" }, "·"), edit);
}

function busy(on, text, which = "run") {
  for (const id of ["run", "preview"]) { $(id).disabled = on; $(id).classList.toggle("busy", on && id === which); }
  if (on) { $("summary").textContent = text; $("kbd-hint").textContent = ""; } else updateSummary();
  if (!on) { $("job-progress").hidden = true; $("cancel").hidden = true; }
  if (!on && jobCancelled) { $("summary").textContent = "Cancelled — nothing was changed."; jobCancelled = false; }
}

// Tools run as background jobs: the bar shows how far they are, and Cancel
// stops them. Resolves with the result's response, or null if cancelled.
let jobId = null, jobCancelled = false;
// Where a job shows how it's going: the tool page's action bar, or a tool in
// the file panel (which brings its own).
const pageJob = {
  start(id) { jobId = id; const cancel = $("cancel"); cancel.hidden = false; cancel.disabled = false; cancel.textContent = "Cancel"; },
  message(text) { $("summary").textContent = text; },
  progress(fraction) {
    const box = $("job-progress"), pct = Math.round(fraction * 100);
    box.hidden = false;
    $("job-bar").style.width = `${pct}%`;
    box.setAttribute("aria-valuenow", String(pct));
  },
  cancelled() { jobCancelled = true; },
  end() { jobId = null; },
};
async function runJob(path, options, ui = pageJob) {
  const start = await (await api(path, { ...options, headers: { ...(options.headers || {}), "X-Pdfm-Async": "1" } })).json();
  return followJob(start.job, ui);
}
/** Follow the job ``id`` (already started) on ``ui`` until it ends: its
 *  result's response, or null if it was cancelled. */
async function followJob(id, ui = pageJob) {
  ui.start(id);
  const shown = Date.now();
  try {
    for (let wait = 150; ; wait = Math.min(wait * 1.5, 600)) {
      await new Promise((r) => setTimeout(r, wait));
      const s = await (await api(`/api/job/${id}`)).json();
      if (s.message) ui.message(s.message);
      if (Date.now() - shown > 400) ui.progress(s.fraction);  // quick jobs don't need a bar
      if (s.state === "cancelled") { ui.cancelled(); return null; }
      if (s.state === "done" || s.state === "failed") break;
    }
    return await api(`/api/job/${id}/result`);
  } finally { ui.end(); }
}

// ---- Explorer's right-click actions (quickactions.py) ------------------------------
// Files chosen together in Explorer arrive one by one; the app gathers them
// into a batch, runs it as a job and the results open as new, unsaved tabs
// (Save asks where, starting in the files' folder). A card at the foot of
// the window shows how it's going, with Cancel.
const actionsRunning = new Set();
async function runAction(batchId) {
  if (!batchId || actionsRunning.has(batchId)) return;
  actionsRunning.add(batchId);
  const msg = el("p", { class: "ac-msg", "aria-live": "polite" }, "Gathering the files…");
  const bar = el("span", { class: "job-progress", role: "progressbar", "aria-label": "Progress", "aria-valuemin": "0", "aria-valuemax": "100", hidden: true }, el("span"));
  const cancel = el("button", { type: "button", class: "btn secondary pb-small" }, "Cancel");
  const title = el("b", { class: "ac-title" }, "ZPDF");
  const card = el("div", { class: "action-card", "data-batch": batchId, role: "group", "aria-label": "Right-click action" }, title, msg, bar, el("div", { class: "ac-buttons" }, cancel));
  $("action-cards").append(card);
  let jobNow = null, stopped = false;
  const finish = (text, error = false) => {
    msg.textContent = text; msg.classList.toggle("err", error);
    bar.hidden = true;
    cancel.textContent = "Close"; cancel.disabled = false;
    cancel.onclick = () => fadeAway(card);
    // (Long enough to notice, and never while it's pointed at or focused.)
    if (!error) {
      const later = () => { if (!card.isConnected) return; if (card.matches(":hover, :focus-within")) setTimeout(later, 2000); else fadeAway(card); };
      setTimeout(later, 20000);
    }
  };
  cancel.onclick = async () => {
    stopped = true;
    cancel.disabled = true; cancel.textContent = "Cancelling…";
    if (jobNow) { try { await api(`/api/job/${jobNow}/cancel`, { method: "POST" }); } catch (e) { /* it may just have finished */ } }
  };
  try {
    let info;
    for (;;) {  // the batch closes a moment after its last file
      info = await (await api(`/api/action/${batchId}`)).json();
      title.textContent = `${info.label}: ${plural(info.files.length, "file")}`;
      if (info.job) break;
      if (info.state !== "collecting") throw new Error("the action didn't start");
      await new Promise((r) => setTimeout(r, 400));
    }
    msg.textContent = "Working…";
    jobNow = info.job;
    const res = await followJob(info.job, {
      start() {}, message(text) { msg.textContent = text; },
      progress(fraction) { const pct = Math.round(fraction * 100); bar.hidden = false; bar.firstChild.style.width = `${pct}%`; bar.setAttribute("aria-valuenow", String(pct)); },
      cancelled() { stopped = true; }, end() { jobNow = null; },
    });
    if (!res) { finish("Cancelled: nothing was made."); return; }
    const out = await res.json();
    await select("reader");
    const opened = [];
    for (const r of out.results) {
      const file = await api(`/api/action/${batchId}/file/${r.n}`);
      const bytes = new Uint8Array(await file.arrayBuffer());
      const tab = await reader.openResult(bytes, r.name, null, null);
      if (!tab) continue;
      const sizes = `${humanSize(r.before).replace(" ", " ")} → ${humanSize(r.size).replace(" ", " ")}`;
      const pct = r.before ? Math.round((1 - r.size / r.before) * 100) : 0;
      tab.made({ op: out.op, label: out.label, from: { name: r.from, fileId: r.from_id },
        sizes: out.action === "compress" && pct >= 5 ? `${sizes}, ${pct}% smaller` : out.action === "combine" || out.action === "images"
          ? `${plural(r.count, "file")}, ${humanSize(r.size).replace(" ", " ")}` : sizes, notes: r.notes || [] });
      if (fp.tab === tab) fpShowNote(tab);
      opened.push(r.name);
    }
    const failed = out.failed.map((f) => `${f.name}: ${f.error}`);
    if (!opened.length) { finish(capital(failed.join("; ") || "nothing could be shown"), true); return; }
    const said = opened.length === 1 ? `Opened ${opened[0]} in a new tab. It isn’t saved yet: Save asks where.`
      : `Opened ${opened.length} results in new tabs. They aren’t saved yet: Save asks where.`;
    if (failed.length) finish(`${said} Not done: ${failed.join("; ")}`, true);
    else { finish(said); reader.toast(said, 6000); }
  } catch (e) {
    finish(stopped ? "Cancelled: nothing was made." : capital(e.message || e), !stopped);
  } finally { actionsRunning.delete(batchId); }
}
$("cancel").addEventListener("click", async () => {
  if (!jobId) return;
  $("cancel").disabled = true; $("cancel").textContent = "Cancelling…";
  try { await api(`/api/job/${jobId}/cancel`, { method: "POST" }); } catch (e) { /* it may just have finished */ }
});
$("run").addEventListener("click", () => run(false));
$("preview").addEventListener("click", () => run(true));

