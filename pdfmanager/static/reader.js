// PDF reader built on pdf.js (bundled in /static/pdfjs, see packaging/update_pdfjs.py).
//
//   const reader = new Reader(hostElement, { token, isDesktop, api, tools, onEdit });
//   await reader.openFile(file, password?)          // a File from a drop or file picker
//   await reader.openBytes(bytes, name, password?)  // raw bytes
// A password that turns out wrong (or none, for a protected file) prompts.
//
// Features: continuous scrolling, page navigation, zoom (presets, Ctrl+wheel,
// pinch), rotation, single/two-page view, thumbnails, outline (bookmarks),
// find with highlighting, text selection, links, form filling, annotating
// (highlight, text, drawing, pictures) and signing, document properties,
// hand tool, dark pages, full screen, printing, saving a copy and handing the
// document to one of the app's tools.
//
// Reader is one object; its parts are in files of their own, each a class
// Reader extends (see Measuring in measuring.js): tabs.js (the open documents,
// switching, closing, the tab bar and the save state shown), annotate.js,
// sign.js, find.js, textedit-ui.js (the text editor box) and measuring.js.
// A tab's state is a TabState (tabstate.js); what the modules share is in
// readerkit.js. Here: building the reader, keys, opening and showing a
// document, the sidebar, saving, printing, pages, dialogs.

import { BASE, DOC_OPTIONS, destroyTask, openDocument } from "./pdfjs.js";
import * as files from "./files.js";
import { Overlay } from "./overlay.js";
import { COLORS, el, fadeOut, heldTimer, ICON, isTyping, MIN_ZOOM, MAX_ZOOM, pdfjs, store, svg, viewer, loadPdfjs } from "./readerkit.js";
import { Annotating } from "./annotate.js";
import { Finding } from "./find.js";
import { Layering } from "./layers.js";
import { Measuring } from "./measuring.js";
import { Signing } from "./sign.js";
import { Tabs } from "./tabs.js";
import { TextEditing } from "./textedit-ui.js";
import { saveAction } from "./tabstate.js";
export { heldTimer, loadPdfjs } from "./readerkit.js";


/** What a protected PDF allows, from pdf.js's list of allowed permissions. */
function permissionText(allowed) {
  const P = pdfjs.PermissionFlag, has = (f) => allowed.includes(f);
  const printing = has(P.PRINT_HIGH_QUALITY) ? "printing" : has(P.PRINT) ? "low-quality printing only" : "no printing";
  const changes = has(P.MODIFY_CONTENTS) ? "any changes" : has(P.MODIFY_ANNOTATIONS) ? "commenting, filling in forms and signing"
    : has(P.FILL_INTERACTIVE_FORMS) ? "filling in forms and signing" : has(P.ASSEMBLE) ? "inserting, deleting and turning pages" : "no changes";
  return `${printing}, ${changes}, ${has(P.COPY) ? "copying allowed" : "no copying"}`;
}

/** pdf.js's form fields ({name: [field...]}, a Map since pdf.js 5) as [name, list] pairs. */
function fieldEntries(fields) {
  return fields instanceof Map ? [...fields.entries()] : Object.entries(fields || {});
}


// Rubber stamps: their words (markup.py draws them in their colours).
const STAMPS = ["APPROVED", "NOT APPROVED", "REVIEWED", "RECEIVED", "DRAFT", "FINAL", "CONFIDENTIAL",
  "FOR COMMENT", "VOID"];
const SHAPES = [["rect", "Rectangle"], ["ellipse", "Ellipse"], ["cloud", "Cloud"], ["arrow", "Arrow"], ["line", "Line"]];
const HIGHLIGHT_COLORS = "yellow=#FFFF98,green=#53FFBC,blue=#80EBFF,pink=#FFCBE6,red=#FF4F5F";


const ZOOM_PRESETS = [
  ["auto", "Automatic"], ["page-fit", "Fit page"], ["page-width", "Fit width"], ["page-actual", "Actual size"], ["true-size", "True paper size"],
  ["0.5", "50%"], ["0.75", "75%"], ["1", "100%"], ["1.25", "125%"], ["1.5", "150%"], ["2", "200%"], ["3", "300%"], ["4", "400%"],
];

// A file opened from this computer comes in pieces: the first this long (a
// file no bigger is whole in it, and is held here in full), the rest as pages
// are shown (the whole file only when a change or save needs it).
const FIRST_BYTES = 4 * 1024 * 1024;
// Thumbnails (see #buildThumbs): the list's padding above its first row; what a
// row is taller than its picture (its padding, the gap and the page number:
// see .rd-thumb in reader.css); how many drawn thumbnails are kept.
const THUMB_PAD = 8, THUMB_EXTRA = 44, THUMBS_KEPT = 150;
// A file read in pieces up to this size is read in full in the background
// once its first page is on screen, and then held like a small file: it can
// be shown again, saved elsewhere or compared even if the file on disk is
// changed or deleted meanwhile. Bigger ones stay in pieces.
const HOLD_BYTES = 64 * 1024 * 1024;
// How long the reader waits, at most, for a piece of a file, for all of it,
// and for pdf.js to open a document (not counting the password prompt): a
// request that never answers mustn't leave the reader waiting for ever.
const PIECE_MS = 60000, WHOLE_MS = 180000, OPEN_MS = 120000;
// pdf.js's words for a file it can't make sense of (damaged, or not a PDF).
const DAMAGED = new Set(["InvalidPDFException", "FormatError", "UnknownErrorException", "MissingPDFException"]);

/** fetch(), given up after ``ms`` (and when ``signal`` aborts). */
function fetchFor(url, options, ms, signal = null) {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(new DOMException("no answer in time", "TimeoutError")), ms);
  const onAbort = () => stop.abort(signal.reason);
  if (signal) { if (signal.aborted) onAbort(); else signal.addEventListener("abort", onAbort, { once: true }); }
  // (no-store: the browser lets only one request at a time for an address
  // through its cache, and pieces of one file must go side by side)
  return fetch(url, { cache: "no-store", ...options, signal: stop.signal }).finally(() => {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  });
}

/** ``promise``, or an error saying ``what`` once ``ms`` have gone by. */
function bounded(promise, ms, what) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(what)), ms); })])
    .finally(() => clearTimeout(timer));
}

/** Make a document that's being closed answer every question with a refusal
 *  (an error with ``closed``). Once pdf.js has torn down a document's link to
 *  its worker, its methods throw ("Cannot read properties of null (reading
 *  'sendWithPromise')") instead of rejecting, and a ``.catch()`` doesn't see
 *  that. pdf.js's own viewer can still be asking the document things (for a
 *  document over 250 pages it hands the page views their pages a batch at a
 *  time, waiting in between, without looking again whether the document is
 *  still shown), and so can work of the reader's still on its way: they all
 *  get a rejection now, which they handle. (All of these methods return
 *  promises.) */
function closeOff(doc) {
  const closed = () => Promise.reject(Object.assign(new Error("the document was closed"), { closed: true }));
  for (let proto = Object.getPrototypeOf(doc); proto && proto !== Object.prototype; proto = Object.getPrototypeOf(proto)) {
    for (const name of Object.getOwnPropertyNames(proto)) {
      if (/^(get|has|save|extract)[A-Z]/.test(name) && typeof Object.getOwnPropertyDescriptor(proto, name).value === "function") {
        Object.defineProperty(doc, name, { value: closed, configurable: true, writable: true });
      }
    }
  }
}

/** Why a piece of a file couldn't be had, from the server's answer; ``gone``
 *  when it won't come back by asking again (the file was moved, deleted or
 *  rewritten). */
async function pieceError(res, tab) {
  if (res.status === 404) return Object.assign(new Error("the file is no longer where it was (moved or deleted)"), { gone: true });
  const stamp = res.headers.get("X-Pdfm-Stamp");
  if (res.ok && stamp && tab.stamp && stamp !== tab.stamp) {
    return Object.assign(new Error("another program changed the file since it was opened here"), { gone: true });
  }
  if (res.status !== 206) return new Error((await res.json().catch(() => ({}))).error || res.statusText || `error ${res.status}`);
  return null;
}
const header = (res, name) => { try { return decodeURIComponent(res.headers.get(name) || ""); } catch (e) { return ""; } };

/** Single keys for the annotate bar's labelled tools, while it's open (n, p, r,
 *  t, e and m are the reader's own: next, previous, rotate, panel, edit text, measure). */
const ANNOT_KEYS = { h: "HIGHLIGHT", c: "NOTE", a: "FREETEXT", d: "INK", s: "SIGN" };

export class Reader extends Tabs(Finding(Signing(Annotating(TextEditing(Measuring(Layering(Object))))))) {
  constructor(host, { token, isDesktop, api, tools = [], onEdit, onAllTools, onCommands, onHome, onChange, recent, onFormHint, onRepair, onForgetRecent, onSaveTab, onUnsaved } = {}) {
    super();
    this.host = host;
    this.token = token;
    this.isDesktop = isDesktop || (() => false);
    this.api = api || (() => null);
    this.tools = tools;
    this.onEdit = onEdit;
    this.onAllTools = onAllTools;
    this.onCommands = onCommands;
    this.onHome = onHome;
    this.onChange = onChange;  // the open documents or the shown one changed (the app's file panel follows)
    this.onUnsaved = onUnsaved;  // (names) => the tabs with unsaved work are these now (closing the window asks)
    this.recent = recent;  // (host) => fills host with the app's Recent list (the start screen's), resolves to how many
    this.onFormHint = onFormHint;  // (tab) => the "looks like a form" check came out for tab, or was dismissed
    this.onRepair = onRepair;  // (tab) => run Repair on a file that can't be shown; resolves when its result is open (or it failed)
    this.onForgetRecent = onForgetRecent;  // (tab) => take a file that can't be shown off the Recent list
    this.onSaveTab = onSaveTab;  // (tab) => save a tab the app made (a tool's result): resolves true once saved
    // Everything that changes which document is shown runs one after the
    // other (see #serial): two loads at once would show the wrong tab or
    // trip over the shared worker.
    this.chain = Promise.resolve();
    this.doc = null;
    this.tabs = [];          // open documents (see openBytes)
    this.activeTab = null;
    this.bytes = null;       // the shown document's bytes
    this.name = "";
    this.password = null;
    this.active = false;
    this.#build();
    if (store.get("pdfm-reader-dark", false)) this.container.classList.add("dark");
  }

  // ---- DOM ------------------------------------------------------------------
  #build() {
    const b = (name, title, attrs = {}) => el("button", { class: "rd-btn", type: "button", title, "aria-label": title, html: svg(name), ...attrs });
    this.root = el("div", { class: "rd", tabindex: "-1" });

    // Toolbar
    this.sidebarBtn = b("sidebar", "Show or hide the sidebar", { "aria-pressed": "true", disabled: true });
    // The document's name, and a dot (with the word, for screen readers) while it has changes not saved.
    this.titleText = el("span", { class: "rd-title-text" });
    this.titleDot = el("span", { class: "rd-dot", title: "Not saved", hidden: true });
    this.titleSr = el("span", { class: "rd-sr", hidden: true }, ", unsaved");
    this.titleEl = el("div", { class: "rd-title" }, this.titleText, this.titleDot, this.titleSr);
    this.prevBtn = b("up", "Previous page", { disabled: true });
    this.nextBtn = b("down", "Next page", { disabled: true });
    this.pageInput = el("input", { type: "text", inputmode: "numeric", "aria-label": "Page number", disabled: true, autocomplete: "off" });
    this.pageCount = el("span", {}, "");
    this.zoomOutBtn = b("minus", "Zoom out (Ctrl −)", { disabled: true });
    this.zoomInBtn = b("plus", "Zoom in (Ctrl +)", { disabled: true });
    this.zoomInput = el("input", { type: "text", class: "rd-zoominput", inputmode: "decimal", "aria-label": "Zoom (type a percentage)",
      title: "Zoom: type a percentage and press Enter; ↑ ↓ change it by 10% (with Shift, 1%)", autocomplete: "off", disabled: true });
    this.zoomMenuBtn = b("down", "Zoom presets", { class: "rd-btn rd-zoommenu-btn", "aria-haspopup": "menu", "aria-expanded": "false", disabled: true });
    this.zoomMenu = el("div", { class: "rd-menu rd-zoommenu", role: "menu", "aria-label": "Zoom", hidden: true });
    this.zoomPreset = "auto";  // the preset the zoom is at, or "custom"
    this.scaleBtn = el("button", { class: "rd-btn rd-scale-btn", type: "button", disabled: true, "aria-expanded": "false",
      title: "Drawing scale, true-size zoom and measuring (M to measure)", html: svg("ruler") + "<span>Scale</span>" });
    this.scaleLabel = this.scaleBtn.querySelector("span");
    // The file panel at the right (the app fills it: this file's facts and tools).
    this.panelBtn = b("panel", "File panel", { class: "rd-btn rd-panel-btn", "aria-expanded": "false", "aria-controls": "rd-side" });
    this.panelBtn.title = "Show or hide the file panel (T)";
    // Changes not saved yet: Save and Undo come out where they're seen.
    this.saveNowBtn = el("button", { class: "rd-btn primary seal rd-savenow", type: "button", hidden: true,
      title: "Save your changes (Ctrl+S)", html: svg("save") + "<span>Save</span>" });
    this.undoNowBtn = b("undo", "Undo the last change (Ctrl+Z)", { hidden: true });
    this.homeBtn = this.onHome ? b("home", "Home: all of ZPDF's tools (your documents stay open)") : null;

    const t = (name, label, title, attrs = {}) => el("button", { class: "rd-btn rd-labelled", type: "button", title, html: svg(name) + `<span>${label}</span>`, ...attrs });
    this.findBtn = b("search", "Find in document (Ctrl+F)", { "aria-pressed": "false" });
    this.annotateBtn = t("pen", "Annotate & sign", "Annotate and sign", { "aria-pressed": "false" });
    this.moreBtn = b("more", "More", { "aria-haspopup": "menu", "aria-expanded": "false" });

    // The toolbar keeps what reading needs; the More menu has the rest, and
    // the file panel this file's tools.
    const toolbar = el("div", { class: "rd-toolbar", role: "toolbar", "aria-label": "Reader" },
      el("div", { class: "rd-group" }, this.homeBtn, this.sidebarBtn),
      this.titleEl,
      el("div", { class: "rd-group" }, this.prevBtn, this.nextBtn,
        el("label", { class: "rd-page" }, this.pageInput, this.pageCount)),
      el("span", { class: "rd-sep" }),
      el("div", { class: "rd-group rd-zoomgroup" }, this.zoomOutBtn, this.zoomInput, this.zoomMenuBtn, this.zoomInBtn),
      el("span", { class: "rd-sep" }),
      this.undoNowBtn, this.saveNowBtn, this.scaleBtn,
      el("div", { class: "rd-group" }, this.findBtn, this.annotateBtn, this.moreBtn, this.panelBtn));

    this._buildFind();  // the find bar (find.js)

    // Annotation bar: the tools used most keep their words (and a single key
    // each, see ANNOT_KEYS); the rest are in the labelled More tools menu.
    const tool = (name, label, mode) => el("button", { class: "rd-btn rd-tool", type: "button", title: label, "aria-pressed": "false", "data-mode": mode, html: svg(name) + `<span>${label}</span>` });
    this.annotTools = [
      tool("pointer", "Select", "NONE"), tool("highlight", "Highlight", "HIGHLIGHT"),
      tool("underline", "Underline", "UNDERLINE"), tool("strike", "Strike out", "STRIKEOUT"),
      tool("note", "Note", "NOTE"), tool("text", "Add text", "FREETEXT"),
      tool("draw", "Draw", "INK"), tool("shapes", "Shapes", "SHAPE"), tool("stamp", "Stamp", "STAMPS"), tool("image", "Add picture", "STAMP"),
      tool("edittext", "Edit text", "EDITTEXT"), tool("imgedit", "Edit pictures", "IMAGES"), tool("link", "Links", "LINKS"),
    ];
    const tip = (mode, title) => { this.annotTools.find((b) => b.dataset.mode === mode).title = title; };
    this.annotTools.find((b) => b.dataset.mode === "NONE").classList.add("compact");
    tip("NONE", "Select: no tool, pick and move annotations (Esc)");
    tip("HIGHLIGHT", "Highlight text: drag over the words (H)");
    tip("FREETEXT", "Add text: click where the words go, then type (A)");
    tip("INK", "Draw freehand on the page (D)");
    tip("STAMP", "Add a picture: choose an image file, then place it");
    tip("UNDERLINE", "Underline text: select the words (or select them first, then click here)");
    tip("STRIKEOUT", "Strike out text: select the words (or select them first, then click here)");
    tip("NOTE", "Add a note: click where it goes, then type your comment (C)");
    tip("SHAPE", "Draw a rectangle, ellipse, cloud, arrow or line: drag on the page (Shift: square, circle, 45°)");
    tip("IMAGES", "Edit the pictures on the page: drag to move, drag a corner to resize (Shift: freely), replace or delete");
    tip("LINKS", "Edit links: click one to change where it goes or delete it; drag a box to add one");
    tip("STAMPS", "Stamp the page (Approved, Draft…): click where it goes, or drag its size");
    this.stampSelect = el("select", { class: "rd-zoom", "aria-label": "Stamp", hidden: true },
      ...STAMPS.map((v) => el("option", { value: v }, v[0] + v.slice(1).toLowerCase())), el("option", { value: "" }, "Your own words…"));
    this.stampSelect.value = store.get("pdfm-stamp", "APPROVED");
    this.stampDated = el("input", { type: "checkbox", checked: store.get("pdfm-stamp-dated", true) });
    this.stampDatedLabel = el("label", { class: "rd-check", hidden: true, title: "Add your name and the date and time under the words" },
      this.stampDated, "Name and date");
    this.shapeSelect = el("select", { class: "rd-zoom", "aria-label": "Shape", hidden: true },
      ...SHAPES.map(([v, label]) => el("option", { value: v }, label)));
    this.shapeSelect.value = store.get("pdfm-shape", "rect");
    tip("EDITTEXT", "Change words on the page: click a line of text (E)");
    this.signBtn = el("button", { class: "rd-btn rd-tool", type: "button", title: "Add your signature (S)", html: svg("sign") + "<span>Sign</span>" });
    this.certSignBtn = el("button", { class: "rd-btn rd-tool compact", type: "button", "data-mode": "CERTSIGN", "aria-pressed": "false",
      title: "Digital ID: sign with a certificate (.pfx or .p12); drag a box where the signature goes", html: svg("badge") + "<span>Digital ID</span>" });
    this.annotTools.push(this.certSignBtn);
    this._buildRedact();  // reader redaction
    this.swatches = COLORS.map(([name, ink, mark], i) => el("button", { class: "rd-swatch", type: "button", title: name, "aria-label": name, "aria-pressed": String(i === 1),
      style: `--ink:${ink};--mark:${mark}`, "data-index": i }));
    this.sizeSelect = el("select", { class: "rd-zoom", "aria-label": "Line thickness or text size" });
    this.undoBtn = b("undo", "Undo (Ctrl+Z)", { disabled: true });
    this.redoBtn = b("redo", "Redo (Ctrl+Y)", { disabled: true });
    this.deleteBtn = b("trash", "Delete the selected annotation (Delete)", { disabled: true });
    this.annotSaveBtn = el("button", { class: "rd-btn rd-annotsave", type: "button", title: "Nothing to save yet (Ctrl+S)", html: svg("save") + "<span>Save</span>" });
    this.annotCloseBtn = b("x", "Close the annotation tools");
    // More tools: a menu of the rest; the one in use shows on its button.
    const byMode = (mode) => this.annotTools.find((t) => t.dataset.mode === mode);
    const inMenu = [["Mark text", ["UNDERLINE", "STRIKEOUT"]], ["Add", ["SHAPE", "STAMPS", "STAMP"]],
      ["Change", ["EDITTEXT", "IMAGES", "LINKS"]], ["Protect", ["CERTSIGN", "REDACT"]]];
    this.moreTools = inMenu.flatMap(([, modes]) => modes.map(byMode));
    for (const item of this.moreTools) {
      item.classList.remove("compact");
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", "false");
      item.removeAttribute("aria-pressed");
      item.tabIndex = -1;
    }
    this.moreToolsMenu = el("div", { class: "rd-menu rd-toolsmenu", role: "menu", "aria-label": "More tools", hidden: true },
      ...inMenu.flatMap(([label, modes], i) => [...(i ? [el("hr")] : []), el("div", { class: "label", "aria-hidden": "true" }, label), ...modes.map(byMode)]));
    this.moreToolsBtn = el("button", { class: "rd-btn rd-tool rd-moretools", type: "button", "aria-haspopup": "menu", "aria-expanded": "false",
      title: "More tools: underline, shapes, stamps, pictures, links, Digital ID, redact", html: svg("more") + "<span>More tools</span>" + svg("down") });
    this.annotbar = el("div", { class: "rd-findbar rd-annotbar", hidden: true, role: "toolbar", "aria-label": "Annotate" },
      ...["NONE", "HIGHLIGHT", "NOTE", "FREETEXT", "INK"].map(byMode), this.signBtn, this.moreToolsBtn,
      el("span", { class: "rd-sep" }),
      el("div", { class: "rd-swatches", role: "group", "aria-label": "Colour" }, ...this.swatches), this.shapeSelect, this.stampSelect, this.stampDatedLabel, this.sizeSelect,
      this.redactHow, this.redactApplyBtn,
      el("span", { class: "rd-sep" }), this.undoBtn, this.redoBtn, this.deleteBtn,
      el("span", { style: "flex:1" }), this.annotSaveBtn, this.annotCloseBtn);

    this._buildScaleBar();

    // Sidebar
    this.thumbsTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "true" }, "Pages");
    this.outlineTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "false", disabled: true }, "Bookmarks");
    this.commentsTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "false" }, "Comments");
    this.authorInput = el("input", { type: "text", class: "rd-author", placeholder: "Your name", "aria-label": "Your name on comments",
      value: store.get("pdfm-author", "") });
    this.commentsList = el("div", { class: "rd-comments", "aria-live": "polite" });
    // -- comment summary and XFDF --
    const commentBtn = (label, title, fn) => el("button", { type: "button", class: "rd-linkbtn", title, onclick: fn }, label);
    // (The file box isn't put on the page: the reader has one file box of its own.)
    const pickXfdf = () => {
      const input = el("input", { type: "file", accept: ".xfdf,.xml" });
      input.addEventListener("change", () => { if (input.files[0]) this._importComments(input.files[0]); });
      input.click();
    };
    const commentActions = el("div", { class: "rd-commentactions" },
      commentBtn("Summary", "A PDF listing every comment, page by page", () => this._commentSummary()),
      commentBtn("Export", "Save the comments as an XFDF file, to send or to add to another copy", () => this._exportComments()),
      commentBtn("Import…", "Add comments from an XFDF file (from Acrobat or another copy)", pickXfdf));
    // -- end of comment summary and XFDF --
    this.commentsPanel = el("div", { class: "rd-panel rd-commentspanel", role: "tabpanel", hidden: true },
      el("label", { class: "rd-authorrow" }, "Comment as", this.authorInput), commentActions, this.commentsList);
    this.thumbsList = el("ol", { class: "rd-thumbs", role: "listbox", "aria-multiselectable": "true", "aria-label": "Pages" });
    this.pickedPages = new Set();  // pages picked in the thumbnails
    this.outlineList = el("ul", { class: "rd-outline" });
    this.pageBar = el("div", { class: "rd-pagebar" });
    this.thumbsPanel = el("div", { class: "rd-panel", role: "tabpanel" }, this.pageBar, this.thumbsList);
    this.outlinePanel = el("div", { class: "rd-panel", role: "tabpanel", hidden: true }, this.outlineList);
    this._buildLayers();  // (the Layers tab: shown for a document with layers, see layers.js)
    this.sidebar = el("aside", { class: "rd-sidebar", hidden: true, "aria-label": "Pages, bookmarks, comments and layers" },
      el("div", { class: "rd-tabs", role: "tablist" }, this.thumbsTab, this.outlineTab, this.commentsTab, this.layersTab),
      this.thumbsPanel, this.outlinePanel, this.commentsPanel, this.layersPanel);

    // Viewer
    this.viewerEl = el("div", { class: "pdfViewer" });
    this.container = el("div", { class: "rd-container", tabindex: "0" }, this.viewerEl);
    this.loading = el("div", { class: "rd-loading", hidden: true }, el("div", { class: "rd-spinner", role: "progressbar", "aria-label": "Loading" }));
    this.fileInput = el("input", { type: "file", accept: ".pdf,application/pdf", hidden: true });
    this.emptyOpenBtn = el("button", { class: "rd-btn primary", type: "button", html: svg("open") + "Open a PDF" });
    this.empty = el("div", { class: "rd-empty" },
      el("div", { class: "rd-empty-card" },
        el("div", { class: "rd-empty-icon", html: svg("book") }),
        el("h2", {}, "Read a PDF"),
        el("p", {}, "Drop a PDF here, or open one from your computer."),
        this.emptyOpenBtn,
        // (Each key with its word stays on one line; the lines come out even.)
        el("p", { class: "keys" }, ...["Ctrl+K commands", "T file panel", "E edit text", "M measure", "Ctrl+F find", "N / P pages"]
          .flatMap((keys, i) => [i ? " · " : "", el("span", {}, keys)]))),
      this.recentBox = el("section", { class: "rd-recent", hidden: true, "aria-label": "Recent files" },
        el("h3", {}, "Recent"), this.recentList = el("div", {})));
    // A file that can't be shown (damaged, or not a PDF): a state that stays
    // for its tab, with a way out (Repair), not a toast that goes.
    this.brokenName = el("h2", { class: "rd-broken-name" });
    this.brokenRepair = el("button", { class: "rd-btn primary seal rd-broken-repair", type: "button", html: svg("wrench") + "<span>Try to repair it</span>" });
    this.brokenForget = el("button", { class: "rd-btn rd-broken-forget", type: "button" }, "Remove from Recent");
    this.brokenClose = el("button", { class: "rd-btn rd-broken-close", type: "button" }, "Close");
    this.brokenSaid = el("p", { class: "rd-broken-said", role: "status" });
    this.broken = el("div", { class: "rd-empty rd-broken", hidden: true },
      el("section", { class: "rd-empty-card rd-broken-card", "aria-labelledby": "rd-broken-name" },
        el("div", { class: "rd-empty-icon rd-broken-icon", html: svg("alert") }),
        Object.assign(this.brokenName, { id: "rd-broken-name" }),
        el("p", { class: "rd-broken-why" }, "ZPDF can’t show this file — it may be damaged."),
        el("div", { class: "rd-broken-actions" }, this.brokenRepair, this.brokenForget, this.brokenClose),
        this.brokenSaid));
    this.main = el("div", { class: "rd-main" }, this.container, this.loading, this.empty, this.broken, this.measPanel);
    // The file panel: beside the pages in a wide window, over them (with a
    // scrim) in a narrow one. The app puts its content in.
    this.side = el("aside", { class: "rd-side", id: "rd-side", role: "region", "aria-label": "This file", hidden: true });
    this.sideScrim = el("div", { class: "rd-side-scrim", hidden: true });
    this.body = el("div", { class: "rd-body" }, this.sidebar, this.main, this.sideScrim, this.side);

    this.menu = el("div", { class: "rd-menu", role: "menu", hidden: true });
    this.ctxMenu = el("div", { class: "rd-menu rd-ctxmenu", role: "menu", hidden: true, "aria-label": "Page" });
    this.tabbar = el("div", { class: "rd-tabbar", role: "tablist", "aria-label": "Open documents", hidden: true });
    // Comparing with another revision of the drawing
    this.overlayName = el("b", {});
    this.overlayMode = el("select", { class: "rd-zoom", "aria-label": "Show" },
      el("option", { value: "both" }, "Both, changes in colour"), el("option", { value: "changes" }, "Changes only"),
      el("option", { value: "this" }, "This version only"), el("option", { value: "other" }, "The other version only"));
    this.overlayOffset = el("input", { type: "number", class: "rd-overlay-offset", value: "0", step: "1", "aria-label": "Page offset",
      title: "When the other version has its sheets in other places: its page = this page plus this number" });
    // Signatures in the document: how they check out
    this.sigText = el("span", { class: "rd-sigstatus" });
    const sigMore = el("button", { class: "rd-btn", type: "button", html: "<span>Details</span>" });
    const sigClose = el("button", { class: "rd-btn", type: "button", title: "Hide", "aria-label": "Hide", html: svg("x") });
    this.sigbar = el("div", { class: "rd-findbar rd-sigbar", hidden: true, role: "status" }, el("span", { class: "rd-sigicon", html: svg("badge") }),
      this.sigText, sigMore, el("span", { style: "flex:1" }), sigClose);
    sigMore.addEventListener("click", () => this.#signatureDetails());
    sigClose.addEventListener("click", () => { this.sigbar.hidden = true; });
    // A printed form with nothing to type in: offer to add its fields.
    const formMake = el("button", { class: "rd-btn primary", type: "button", html: svg("fillable") + "<span>Make it fillable</span>" });
    const formClose = el("button", { class: "rd-btn", type: "button", title: "Hide", "aria-label": "Hide", html: svg("x") });
    this.formText = el("span", { class: "rd-sigstatus" });
    this.formbar = el("div", { class: "rd-findbar rd-formbar", hidden: true, role: "status" }, el("span", { class: "rd-sigicon", html: svg("fillable") }),
      this.formText, formMake, el("span", { style: "flex:1" }), formClose);
    formMake.addEventListener("click", () => this.makeFillable());
    formClose.addEventListener("click", () => {
      this.formbar.hidden = true;
      if (this.activeTab) { this.activeTab.formHint = "hidden"; this.onFormHint?.(this.activeTab); }
    });
    const overlayClose = el("button", { class: "rd-btn", type: "button", title: "Stop comparing", "aria-label": "Stop comparing", html: svg("x") });
    this.overlaybar = el("div", { class: "rd-findbar rd-overlaybar", hidden: true, role: "toolbar", "aria-label": "Compare revisions" },
      el("span", { class: "rd-overlay-title" }, "Comparing with ", this.overlayName),
      el("span", { class: "rd-legend" }, el("i", { class: "other" }), "only in that one"),
      el("span", { class: "rd-legend" }, el("i", { class: "this" }), "only in this one"),
      el("span", { class: "rd-sep" }), el("label", {}, "Show", this.overlayMode),
      el("label", {}, "Its page = this page +", this.overlayOffset),
      el("span", { style: "flex:1" }), overlayClose);
    this.overlayMode.addEventListener("change", () => { if (this.overlay) { this.overlay.mode = this.overlayMode.value; this.#drawOverlays(); } });
    this.overlayOffset.addEventListener("change", () => {
      if (!this.overlay) return;
      this.overlay.offset = Math.round(Number(this.overlayOffset.value) || 0);
      this.#drawOverlays();
    });
    overlayClose.addEventListener("click", () => this.closeCompare());
    // Skip link: past the bars, to the pages (out of sight until it has focus).
    this.skipLink = el("a", { class: "skip-link rd-skip", href: "#", onclick: (e) => { e.preventDefault(); this.container.focus(); } }, "Skip to the page");
    this.root.append(this.skipLink, toolbar, this.tabbar, this.findbar, this.annotbar, this.scalebar, this.overlaybar, this.sigbar, this.formbar, this.body, this.menu, this.ctxMenu, this.zoomMenu, this.moreToolsMenu, this.fileInput);
    // A drawing in progress takes every click in the window as the next
    // stroke; end it first when the tabs, toolbar or menu are used. This
    // listens on the window before pdf.js does, so it runs first.
    const outside = [this.tabbar, toolbar, this.side, this.scalebar, this.menu, this.ctxMenu];
    window.addEventListener("pointerdown", (e) => {
      if (!outside.some((area) => area.contains(e.target))) return;
      this._finishEditing();
    }, { capture: true });
    this.host.append(this.root);
    this.#wire();
  }

  #menuItem(icon, label, onclick, checkable = null) {
    const attrs = { type: "button", role: checkable === null ? "menuitem" : "menuitemcheckbox" };
    if (checkable !== null) attrs["aria-checked"] = String(!!checkable);
    const btn = el("button", attrs);
    btn.innerHTML = svg(icon);
    btn.append(el("span", {}, label));
    if (checkable !== null) btn.append(el("span", { class: "check", html: svg("check") }));
    btn.addEventListener("click", () => { this.#closeMenu(); onclick(); });
    return btn;
  }

  #renderMenu() {
    const has = !!this.doc;
    const spread = this.pdfViewer && this.pdfViewer.spreadMode !== viewer.SpreadMode.NONE;
    // (Open and the commands work without a document too.)
    const always = [this.#menuItem("open", "Open a PDF…", () => this.chooseFile()),
      ...(this.onCommands ? [this.#menuItem("command", "Search commands and tools…", () => this.onCommands())] : [])];
    const items = [
      ...always,
      el("hr"),
      this.#menuItem("cw", "Rotate the view clockwise", () => this.rotate(90)),
      this.#menuItem("ccw", "Rotate the view counter-clockwise", () => this.rotate(-90)),
      this.#menuItem("spread", "Two-page view", () => this.toggleSpread(), spread),
      this.#menuItem("hand", "Hand tool (drag to scroll)", () => this.toggleHand(), this.container.classList.contains("hand")),
      this.#menuItem("moon", "Dark pages", () => this.toggleDarkPages(), this.container.classList.contains("dark")),
      el("hr"),
      this.#menuItem("full", "Full screen", () => this.toggleFullscreen()),
      this.#menuItem("print", "Print…", () => this.print()),
      ...(this.activeTab?.fileId ? [this.#menuItem("save", "Save", () => this.save())] : []),
      this.#menuItem("save", this.activeTab?.fileId ? "Save as…" : this.#edited() ? "Save a copy with your changes…" : "Save a copy…", () => this.saveCopy()),
      this.#menuItem("layers", this.overlay ? "Stop comparing revisions" : "Compare with another revision…", () => (this.overlay ? this.closeCompare() : this.compareRevision())),
      this.#menuItem("badge", "Digital ID…", () => this.useTool("CERTSIGN")),
      this.#menuItem("fillable", "Make it fillable (find the form's fields)", () => this.makeFillable()),
      this.#menuItem("info", "Document properties", () => this.showProperties()),
      this.#menuItem("access", "Check accessibility", () => this.checkAccessibility()),  // -- accessibility --
      this.#menuItem("x", "Close this document", () => this.closeTab()),
    ];
    if (this.onEdit && this.tools.length) {
      items.push(el("hr"), el("div", { class: "label" }, "Edit with"));
      for (const [id, label] of this.tools) items.push(this.#menuItem("tool", label, () => this.#edit(id)));
    }
    for (const item of items) if (item.tagName === "BUTTON" && !always.includes(item)) item.disabled = !has;
    this.menu.replaceChildren(...items);
  }

  #toggleMenu() {
    if (!this.menu.hidden) return this.#closeMenu();
    this.#renderMenu();
    // Open under the More button.
    const at = this.moreBtn.getBoundingClientRect(), box = this.root.getBoundingClientRect();
    this.menu.style.top = `${at.bottom - box.top + 6}px`;
    this.menu.hidden = false;
    this.moreBtn.setAttribute("aria-expanded", "true");
    const first = this.menu.querySelector("button:not(:disabled)");
    if (first) first.focus();
  }
  #closeMenu() { this.menu.hidden = true; this.moreBtn.setAttribute("aria-expanded", "false"); }

  // ---- zoom -------------------------------------------------------------------
  /** Show the zoom in its box: the percentage, and which preset it is. */
  #showZoom(scale = this.pdfViewer?.currentScale) {
    if (!this.doc || !Number.isFinite(scale)) return;
    this.zoomInput.value = Math.round(scale * 100) + "%";
    const preset = ZOOM_PRESETS.find(([v]) => v === this.zoomPreset);
    this.zoomMenuBtn.title = preset ? `Zoom presets (now: ${preset[1]})` : "Zoom presets";
  }

  /** Zoom to ``factor`` (1 = 100%), kept to what the viewer can draw. */
  setZoom(factor) {
    if (!this.doc || !Number.isFinite(factor)) return false;
    const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, factor));
    this.pdfViewer.currentScale = zoom;
    if (zoom !== factor) this.toast(`Zoom goes from ${MIN_ZOOM * 100}% to ${MAX_ZOOM * 100}%.`, 2500);
    return true;
  }

  /** The zoom typed into the box: 137, 137%, 137.5 (or 1.37, as a factor). */
  #applyTypedZoom() {
    const text = this.zoomInput.value.trim().replace(",", ".");
    const m = text.match(/^(\d+(?:\.\d+)?)\s*%?$/);
    const n = m ? parseFloat(m[1]) : NaN;
    if (!(n > 0)) { this.#showZoom(); if (text) this.toast("Type a zoom such as 150 or 66.7%."); return; }
    this.setZoom(n < 10 && !text.includes("%") ? n : n / 100);
    this.#showZoom();
  }

  #toggleZoomMenu() {
    if (!this.zoomMenu.hidden) return this.#closeZoomMenu();
    if (!this.doc) return;
    const items = [];
    for (const [value, label] of ZOOM_PRESETS) {
      if (value === "0.5") items.push(el("hr"));
      const item = el("button", { type: "button", role: "menuitemradio", "aria-checked": String(value === this.zoomPreset) },
        el("span", {}, label), el("span", { class: "check", html: svg("check") }));
      item.addEventListener("click", () => {
        this.#closeZoomMenu();
        if (value === "true-size") this._viewAt("true");
        else this.pdfViewer.currentScaleValue = value;
        this.container.focus();
      });
      items.push(item);
    }
    this.zoomMenu.replaceChildren(...items);
    const at = this.zoomInput.getBoundingClientRect(), box = this.root.getBoundingClientRect();
    this.zoomMenu.style.top = `${at.bottom - box.top + 6}px`;
    this.zoomMenu.style.left = `${Math.max(8, at.left - box.left)}px`;
    this.zoomMenu.style.right = "auto";
    this.zoomMenu.hidden = false;
    this.zoomMenuBtn.setAttribute("aria-expanded", "true");
    (this.zoomMenu.querySelector("[aria-checked=true]") || this.zoomMenu.querySelector("button")).focus();
  }
  #closeZoomMenu() { this.zoomMenu.hidden = true; this.zoomMenuBtn.setAttribute("aria-expanded", "false"); }

  // ---- events -----------------------------------------------------------------
  #wire() {
    this.emptyOpenBtn.addEventListener("click", () => this.chooseFile());
    this.brokenRepair.addEventListener("click", () => this.#repairBroken());
    this.brokenForget.addEventListener("click", () => this.#forgetBroken());
    this.brokenClose.addEventListener("click", () => this.closeTab(this.activeTab));
    this.fileInput.addEventListener("change", () => { const f = this.fileInput.files[0]; this.fileInput.value = ""; if (f) this.openFile(f); });
    this.sidebarBtn.addEventListener("click", () => this.toggleSidebar());
    this.prevBtn.addEventListener("click", () => this.pdfViewer.previousPage());
    this.nextBtn.addEventListener("click", () => this.pdfViewer.nextPage());
    this.pageInput.addEventListener("change", () => this.#goToTypedPage());
    this.pageInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { this.#goToTypedPage(); this.container.focus(); } });
    this.pageInput.addEventListener("focus", () => this.pageInput.select());
    this.zoomInBtn.addEventListener("click", () => this.pdfViewer.increaseScale());
    this.zoomOutBtn.addEventListener("click", () => this.pdfViewer.decreaseScale());
    this.zoomInput.addEventListener("focus", () => this.zoomInput.select());
    this.zoomInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this.#applyTypedZoom(); this.container.focus(); }
      else if (e.key === "Escape") { e.preventDefault(); this.#showZoom(); this.container.focus(); }
      else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        const step = (e.shiftKey ? 1 : 10) * (e.key === "ArrowUp" ? 1 : -1);
        this.setZoom((Math.round(this.pdfViewer.currentScale * 100) + step) / 100);
        this.zoomInput.select();
      }
    });
    this.zoomInput.addEventListener("change", () => this.#applyTypedZoom());
    this.zoomMenuBtn.addEventListener("click", () => this.#toggleZoomMenu());
    document.addEventListener("pointerdown", (e) => { if (!this.zoomMenu.hidden && !this.zoomMenu.contains(e.target) && !this.zoomMenuBtn.contains(e.target)) this.#closeZoomMenu(); });
    this.scaleBtn.addEventListener("click", () => this.toggleScaleBar());
    this.findBtn.addEventListener("click", () => (this.findbar.hidden ? this.openFind() : this.closeFind()));
    this.moreBtn.addEventListener("click", () => this.#toggleMenu());
    this.panelBtn.addEventListener("click", () => this.togglePanel());
    this.sideScrim.addEventListener("click", () => this.setPanel(false, { focus: true }));
    // A narrow window shows the panel over the pages; it closes on Escape.
    this.side.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !this.#panelDocked() && !document.querySelector("dialog[open]")) { e.preventDefault(); e.stopPropagation(); this.setPanel(false, { focus: true }); }
    });
    this.wideQuery = matchMedia("(min-width: 1100px)");
    this.wideQuery.addEventListener("change", () => this.#applyPanel());
    this.saveNowBtn.addEventListener("click", () => this.save());
    this.undoNowBtn.addEventListener("click", () => this._undo());
    this.homeBtn?.addEventListener("click", () => this.onHome());
    document.addEventListener("pointerdown", (e) => { if (!this.menu.hidden && !this.menu.contains(e.target) && e.target !== this.moreBtn && !this.moreBtn.contains(e.target)) this.#closeMenu(); });
    this.thumbsTab.addEventListener("click", () => this.#showTab("thumbs"));
    this.outlineTab.addEventListener("click", () => this.#showTab("outline"));
    this.commentsTab.addEventListener("click", () => this.#showTab("comments"));
    this.authorInput.addEventListener("change", () => store.set("pdfm-author", this.authorInput.value.trim()));
    this.shapeSelect.addEventListener("change", () => store.set("pdfm-shape", this.shapeSelect.value));

    this._wireFind();

    // Annotating
    this.annotateBtn.addEventListener("click", () => (this.annotbar.hidden ? this.openAnnotate() : this.closeAnnotate()));
    for (const btn of this.annotTools) btn.addEventListener("click", () => {
      const fromMenu = !this.moreToolsMenu.hidden && this.moreToolsMenu.contains(btn);
      if (fromMenu) this._closeToolsMenu(true);
      this._setEditMode(btn.dataset.mode, btn.dataset.mode === "STAMP");
    });
    this.signBtn.addEventListener("click", () => this.sign());
    this.moreToolsBtn.addEventListener("click", () => this._toggleToolsMenu());
    this.moreToolsBtn.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (this.moreToolsMenu.hidden) this._toggleToolsMenu(e.key === "ArrowUp" ? "last" : "checked"); }
    });
    // In the menu: arrows, Home and End move; Escape (or Tab) closes it, focus back on its button.
    this.moreToolsMenu.addEventListener("keydown", (e) => {
      const items = this.moreTools, at = items.indexOf(document.activeElement);
      const go = (i) => { e.preventDefault(); items[(i + items.length) % items.length].focus(); };
      if (e.key === "ArrowDown") go(at + 1);
      else if (e.key === "ArrowUp") go(at < 0 ? items.length - 1 : at - 1);
      else if (e.key === "Home") go(0);
      else if (e.key === "End") go(items.length - 1);
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); this._closeToolsMenu(true); }
      else if (e.key === "Tab") this._closeToolsMenu();
    });
    document.addEventListener("pointerdown", (e) => {
      if (!this.moreToolsMenu.hidden && !this.moreToolsMenu.contains(e.target) && !this.moreToolsBtn.contains(e.target)) this._closeToolsMenu();
    });
    for (const sw of this.swatches) sw.addEventListener("click", () => this._setColor(+sw.dataset.index));
    this.sizeSelect.addEventListener("change", () => this._setSize(+this.sizeSelect.value));
    this.undoBtn.addEventListener("click", () => this._undo());
    this.redoBtn.addEventListener("click", () => this._undo(true));
    this.deleteBtn.addEventListener("click", () => this.uiManager?.delete());
    this.annotSaveBtn.addEventListener("click", () => this.save());
    this.annotCloseBtn.addEventListener("click", () => this.closeAnnotate());

    // Drop a PDF anywhere on the reader to open it.
    const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
    this.root.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.stopPropagation(); this.empty.classList.add("over"); } });
    this.root.addEventListener("dragleave", (e) => { if (!this.root.contains(e.relatedTarget)) this.empty.classList.remove("over"); });
    this.root.addEventListener("drop", (e) => {
      this.empty.classList.remove("over");
      if (!hasFiles(e)) return;
      e.preventDefault(); e.stopPropagation();
      const isPdf = (f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name);
      const file = [...e.dataTransfer.files].find(isPdf);
      if (!file) { this.toast("That isn't a PDF."); return; }
      // Remember dropped files for Recent (the handle must be asked for right away).
      const item = [...e.dataTransfer.items].find((i) => i.kind === "file" && i.getAsFile() && isPdf(i.getAsFile()));
      const handle = item && item.getAsFileSystemHandle ? item.getAsFileSystemHandle() : null;
      if (handle) handle.then((h) => h && h.kind === "file" && files.rememberHandle(h)).catch(() => {});
      this.openFile(file);
    });

    // Ctrl/⌘ + wheel zooms around the pointer.
    this.container.addEventListener("wheel", (e) => {
      if (!(e.ctrlKey || e.metaKey) || !this.doc) return;
      e.preventDefault();
      const origin = [e.clientX, e.clientY];
      const factor = Math.exp(-e.deltaY / (e.deltaMode === 1 ? 20 : 300));
      this.pdfViewer.updateScale({ drawingDelay: 300, scaleFactor: Math.min(1.5, Math.max(0.67, factor)), origin });
    }, { passive: false });

    // Hand tool: drag to scroll.
    let drag = null;
    this.container.addEventListener("pointerdown", (e) => {
      if (!this.container.classList.contains("hand") || this.textEditMode || e.button !== 0 || e.target.closest("a, input, textarea, select, button")) return;
      drag = { x: e.clientX, y: e.clientY, left: this.container.scrollLeft, top: this.container.scrollTop };
      this.container.classList.add("grabbing");
      this.container.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    this.container.addEventListener("pointermove", (e) => {
      if (!drag) return;
      this.container.scrollLeft = drag.left - (e.clientX - drag.x);
      this.container.scrollTop = drag.top - (e.clientY - drag.y);
    });
    const endDrag = () => { drag = null; this.container.classList.remove("grabbing"); };
    this.container.addEventListener("pointerup", endDrag);
    this.container.addEventListener("pointercancel", endDrag);

    // Right-click a page (or its thumbnail) for what can be done with it.
    this.container.addEventListener("contextmenu", (e) => this.#pageMenu(e));
    this.thumbsList.addEventListener("contextmenu", (e) => this.#pageMenu(e));
    document.addEventListener("pointerdown", (e) => { if (!this.ctxMenu.hidden && !this.ctxMenu.contains(e.target)) this.#closeContextMenu(); }, true);
    this.container.addEventListener("scroll", () => this.#closeContextMenu(), { passive: true });
    this.#wireThumbs();

    // Measuring: clicks on a page place the points.
    this.container.addEventListener("pointerdown", (e) => this._measureDown(e), true);
    this.container.addEventListener("pointermove", (e) => this._measureMove(e));
    window.addEventListener("pointerup", () => this._measureUp());

    // Edit text tool: click a line of text to change it.
    this.container.addEventListener("click", (e) => this._textClick(e), true);

    // Select (no tool): a click on a signature, drawing, text box or
    // highlight picks it (pdf.js's own way is a double-click).
    // (Pressed and let go without moving: no "click" event, which the browser
    // doesn't always send when pdf.js's text layer moves things under the
    // pointer meanwhile.)
    let pressed = null;
    this.container.addEventListener("pointerdown", (e) => { pressed = e.isPrimary ? [e.clientX, e.clientY, e.timeStamp] : null; }, true);
    this.container.addEventListener("pointerup", (e) => {
      const still = pressed && Math.hypot(e.clientX - pressed[0], e.clientY - pressed[1]) < 5 && e.timeStamp - pressed[2] < 800;
      pressed = null;
      if (still) this._pickOnClick(e);
    });

    // Notes and shapes: click or drag on a page; underline and strike out: select words.
    this.container.addEventListener("pointerdown", (e) => this._markupDown(e), true);
    this.container.addEventListener("pointermove", (e) => this._markupMove(e));
    window.addEventListener("pointerup", (e) => this._markupUp(e));

    document.addEventListener("keydown", (e) => this.#onKey(e));
  }

  #onKey(e) {
    if (!this.active || document.querySelector("dialog[open]")) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "o") { e.preventDefault(); this.chooseFile(); return; }
    if (e.key === "Escape" && !this.menu.hidden) { this.#closeMenu(); this.moreBtn.focus(); return; }
    if (e.key === "Escape" && !this.zoomMenu.hidden) { this.#closeZoomMenu(); this.zoomMenuBtn.focus(); return; }
    if (!this.doc) return;
    if (e.key === "Escape" && this.panelOpen && !this.#panelDocked()) { e.preventDefault(); this.setPanel(false, { focus: true }); return; }
    if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); this.openFind(); return; }
    if (mod && e.key.toLowerCase() === "p") { e.preventDefault(); this.print(); return; }
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); this._saveKey(e); return; }
    if (mod && !isTyping(e) && !this.scalebar.hidden && (e.key.toLowerCase() === "z" || e.key.toLowerCase() === "y")
        && this._measureUndo(e.key.toLowerCase() === "y" || e.shiftKey)) { e.preventDefault(); return; }
    // Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z): what the Undo and Redo buttons do, with
    // any tool on or none (with Select, pdf.js has no keys of its own; with
    // its tools on, it doesn't hear them as well: stopPropagation).
    if (mod && !e.altKey && (e.key.toLowerCase() === "z" || e.key.toLowerCase() === "y") && !isTyping(e)
        && this._undo(e.key.toLowerCase() === "y" || e.shiftKey)) { e.preventDefault(); e.stopPropagation(); return; }
    if (e.key === "Escape" && !this.ctxMenu.hidden) { this.#closeContextMenu(); return; }
    if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); this.pdfViewer.increaseScale(); return; }
    if (mod && e.key === "-") { e.preventDefault(); this.pdfViewer.decreaseScale(); return; }
    if (mod && e.key === "0") { e.preventDefault(); this.pdfViewer.currentScaleValue = "auto"; return; }
    if (mod || e.altKey || isTyping(e)) return;
    if (this.measureMode && this._measureKey(e)) { e.preventDefault(); return; }
    if (e.key === "Escape" && !this.findbar.hidden) { this.closeFind(); return; }
    if (this.markupMode === "IMAGES" && this.pickedImage && !isTyping(e) && (e.key === "Delete" || e.key === "Backspace")) {
      e.preventDefault();
      this._editImage("delete");
      return;
    }
    if (e.key === "Escape" && this.markupMode === "IMAGES" && this.pickedImage) { this._pickImage(null); return; }
    if (e.key === "Escape" && this.editMode && this.editMode !== "NONE") { this._setEditMode("NONE"); return; }
    // With no tool left on, Escape closes the annotate bar (focus back on its button).
    if (e.key === "Escape" && !this.annotbar.hidden) { this.closeAnnotate(); if (this.findbar.hidden) this.annotateBtn.focus(); return; }
    const v = this.pdfViewer;
    switch (e.key) {
      case "n": case "j": v.nextPage(); break;
      case "p": case "k": v.previousPage(); break;
      case "r": this.rotate(90); break;
      case "R": this.rotate(-90); break;
      case "t": this.togglePanel(); break;
      case "e": this.useTool(this.textEditMode ? "NONE" : "EDITTEXT"); break;
      case "m": this.measureTool(this.measureMode === "distance" ? null : "distance"); break;
      // The annotate bar's labelled tools, while it's open (H, C, A, D, S; again: back to Select).
      case "h": case "c": case "a": case "d": case "s": {
        if (this.annotbar.hidden || e.shiftKey) return;
        const mode = ANNOT_KEYS[e.key];
        if (mode === "SIGN") this.sign();
        else this._setEditMode(this.editMode === mode ? "NONE" : mode);
        break;
      }
      case "Home": v.currentPageNumber = 1; break;
      case "End": v.currentPageNumber = v.pagesCount; break;
      case "ArrowLeft": if (this.container.scrollWidth <= this.container.clientWidth) v.previousPage(); else return; break;
      case "ArrowRight": if (this.container.scrollWidth <= this.container.clientWidth) v.nextPage(); else return; break;
      default: return;
    }
    e.preventDefault();
  }

  // ---- visibility (called by the app when switching tools) --------------------
  show() {
    this.active = true; this.root.hidden = false;
    if (this.doc) this.container.focus({ preventScroll: true });
    // (A moment later: when the reader is shown to open a file in it, the
    // Recent list isn't needed, and listing it would keep the server busy
    // while the file is being read.)
    else setTimeout(() => { if (!this.doc && !this.opening) this.refreshRecent(); }, 0);
  }
  hide() { this.active = false; this.root.hidden = true; this.#closeMenu(); }

  // ---- opening ------------------------------------------------------------------
  async chooseFile() {
    const api = this.api();
    if (this.isDesktop() && api && api.open_pdf) {
      try {
        const picked = await api.open_pdf();
        if (picked) await this.openById(picked.id);
      } catch (e) { this.toast(String(e.message || e)); }
      return;
    }
    if (files.canOpen) {
      // The browser's Open dialog gives a handle, so the file can be reopened from Recent.
      try {
        const [picked] = await files.openPdfs(false);
        if (picked) { files.rememberHandle(picked.handle); await this.openFile(picked.file); }
      } catch (e) { this.fileInput.click(); }
      return;
    }
    this.fileInput.click();
  }

  // ---- recent files ---------------------------------------------------------------
  /** Under "Open a PDF": the app's Recent list, the same rows as on its start
   *  screen (files opened by path, with their facts, and by browser handle). */
  async refreshRecent() {
    if (!this.recent) { this.recentBox.hidden = true; return; }
    let count = 0;
    try { count = await this.recent(this.recentList); } catch (e) { count = 0; }
    this.recentBox.hidden = !count || !!this.doc;
  }

  /** Open a file the server knows by id; ``goto`` = {page, find} shows a
   *  page and searches for text on it (e.g. from the folder search). */
  async openById(id, goto = null, first = null) {
    this.#busy(true);
    this.opening = (this.opening || 0) + 1;
    try {
      const url = "/api/file/" + encodeURIComponent(id);
      // One request: the file's first piece, which comes with its size, place
      // and version. A small file is whole in it. (``first``: that request,
      // already made by the page while it was starting.)
      const res = await (first || fetch(url, { cache: "no-store", headers: { "X-Pdfm-Token": this.token, Range: `bytes=0-${FIRST_BYTES - 1}` } }));
      if (!res.ok && res.status !== 416) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const head = new Uint8Array(await res.arrayBuffer());
      const name = header(res, "X-Pdfm-Filename") || "document.pdf";
      const file = { fileId: id, path: header(res, "X-Pdfm-Path"), stamp: res.headers.get("X-Pdfm-Stamp") || "",
        size: Number((res.headers.get("Content-Range") || "").split("/")[1]) || head.length };
      if (head.length >= file.size) await this.openBytes(head, name, null, goto, file);
      else await this._serial(() => this._openBytesNow(null, name, null, goto, { ...file, url, head, headCopy: head.slice() }));  // the rest in pieces
    } catch (e) {
      this.toast("Could not open the file: " + (e.message || e));
    } finally {
      this.opening--;
      if (!this.opening && !this.doc && this.active) this.refreshRecent();  // nothing opened after all
      this.#busy(false);
    }
  }

  /** ``tab`` holds a file that can't be shown: say so where its pages would
   *  be, with Try to repair it, Remove from Recent and Close. It stays (for
   *  this tab) until the tab is closed. */
  #showBroken(tab) {
    this.activeTab = tab;
    this.#busy(false);
    this.empty.hidden = true;
    this.broken.hidden = false;
    this.brokenName.textContent = tab.name;
    this.brokenName.title = tab.path || tab.name;
    this.brokenRepair.hidden = !this.onRepair || (!tab.fileId && !tab.original);
    this.brokenForget.hidden = !this.onForgetRecent || !tab.path || tab.forgotten;
    this.brokenSaid.textContent = tab.brokenSaid || "";
    this.pageInput.value = "";
    this.pageCount.textContent = "";
    for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
      this.zoomInput, this.zoomMenuBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = true;
    this.zoomInput.value = "";
    this.sidebar.hidden = true;
    this.root.classList.remove("reading");
    this._refresh();
  }

  async #repairBroken() {
    const tab = this.activeTab;
    if (!tab?.broken || !this.onRepair || this.brokenRepair.disabled) return;
    this.brokenRepair.disabled = true;
    this.brokenRepair.classList.add("busy");
    this.brokenRepair.lastChild.textContent = "Repairing…";
    tab.say("");
    this.brokenSaid.textContent = "";
    try {
      const said = await this.onRepair(tab);
      if (said) { tab.say(said); if (this.activeTab === tab) this.brokenSaid.textContent = said; }
    } finally {
      this.brokenRepair.disabled = false;
      this.brokenRepair.classList.remove("busy");
      this.brokenRepair.lastChild.textContent = "Try to repair it";
    }
  }

  async #forgetBroken() {
    const tab = this.activeTab;
    if (!tab?.broken || !this.onForgetRecent) return;
    try { await this.onForgetRecent(tab); } catch (e) { this.toast("Couldn't remove it from Recent: " + (e.message || e)); return; }
    tab.forgot();
    this.brokenForget.hidden = true;
    tab.say(`Removed ${tab.name} from Recent.`);
    this.brokenSaid.textContent = tab.brokenSaid;
    this.brokenClose.focus();
  }

  _showEmpty() {
    this.activeTab = null;
    this.#busy(false);
    this.broken.hidden = true;
    this.empty.hidden = false;
    this.pageInput.value = "";
    this.pageCount.textContent = "";
    for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
      this.zoomInput, this.zoomMenuBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = true;
    this.zoomInput.value = "";
    this.sidebar.hidden = true;
    this.root.classList.remove("reading");
    setTimeout(() => { if (!this.doc && !this.opening) this.refreshRecent(); }, 0);  // (see show())
    this._refresh();
  }

  /** The first piece of a file that is read in pieces (``tab.url``), asked
   *  for again when its tab is shown after another one (the piece that came
   *  when the file was opened went to pdf.js). The file must still be the
   *  one that was opened: a file changed or deleted meanwhile is said so,
   *  never shown in its place. */
  async #firstPiece(tab) {
    if (tab.gone) throw Object.assign(new Error(tab.gone), { gone: true });
    let res;
    try {
      res = await fetchFor(tab.url, { headers: { "X-Pdfm-Token": this.token, Range: `bytes=0-${FIRST_BYTES - 1}` } }, PIECE_MS);
    } catch (e) { throw new Error("the file couldn't be read: " + (e.message || e)); }
    const bad = await pieceError(res, tab);
    const size = Number((res.headers.get("Content-Range") || "").split("/")[1]) || 0;
    if (bad || size !== tab.size) {
      const e = bad || Object.assign(new Error("another program changed the file since it was opened here"), { gone: true });
      if (e.gone) tab.fileGone(e.message);
      throw e;
    }
    tab.firstPiece(new Uint8Array(await res.arrayBuffer()));
  }

  /** Where pdf.js gets the pieces of a file that isn't held here in full:
   *  it starts with the first piece (``tab.head``, which it takes) and asks
   *  for the others as pages need them; once all of the file is here
   *  (#readAll), they come from memory. A piece that fails is asked for once
   *  more; if it fails again, ``failed`` rejects (#load gives up opening)
   *  and, once open, the reader says so once and offers to close the tab.
   *
   *  pdf.js has no way to be told a piece can't be had: it would wait for
   *  it for ever, and so would closing the document. So a piece given up
   *  (or still on its way when the document is closed: ``giveUp``) is
   *  answered with empty bytes, which pdf.js reads as nothing to draw; the
   *  document is then ``poisoned``: it is never written out (#savedDocument). */
  #pieces(tab) {
    const source = new pdfjs.PDFDataRangeTransport(tab.size, tab.head);
    const stop = new AbortController();
    const pending = new Map();  // begin -> end of the pieces pdf.js waits for
    source.failed = new Promise((_, reject) => { source.fail = reject; });
    source.failed.catch(() => {});
    source.serve = (data) => {  // all of the file is here
      for (const [begin, end] of pending) source.onDataRange(begin, data.slice(begin, end));
      pending.clear();
    };
    source.giveUp = () => {
      source.poisoned = true;
      stop.abort();
      for (const [begin, end] of pending) source.onDataRange(begin, new Uint8Array(end - begin));
      pending.clear();
    };
    const ask = async (begin, end, again) => {
      try {
        const res = await fetchFor(tab.url, { headers: { "X-Pdfm-Token": this.token, Range: `bytes=${begin}-${end - 1}` } }, PIECE_MS, stop.signal);
        const bad = await pieceError(res, tab);
        if (bad) throw bad;
        const chunk = new Uint8Array(await res.arrayBuffer());
        if (chunk.length !== end - begin) throw new Error("the file is shorter than it was");
        if (pending.delete(begin)) source.onDataRange(begin, chunk);
      } catch (e) {
        if (stop.signal.aborted || !pending.has(begin)) return;
        if (source.data) { source.serve(source.data); return; }
        if (again && !e.gone) { setTimeout(() => ask(begin, end, false), 300); return; }
        if (e.gone) tab.fileGone(e.message);
        if (source.failure) { source.giveUp(); return; }
        source.failure = String(e.message || e);
        source.fail(new Error(source.failure));
        source.giveUp();
        if (source.task && source.task === this.task && this.doc && this.activeTab === tab) {
          this.toast(`Couldn't read more of “${tab.name}”: ${source.failure}. Close it and open it again.`, 15000,
            ["Close", () => this.closeTab(tab)]);
        }
      }
    };
    source.requestDataRange = (begin, end) => {
      pending.set(begin, end);
      if (source.data) source.serve(source.data);
      else if (source.poisoned) source.giveUp();
      else ask(begin, end, true);
    };
    source.abort = () => { stop.abort(); pending.clear(); };
    source.waiting = () => pending.size > 0;
    tab.piecesFrom(source);  // (pdf.js takes the first piece)
    return source;
  }

  /** All of a file read in pieces, read now (once; at most HOLD_BYTES of it
   *  in the background, any size when a change or a save needs it). From
   *  then on the tab holds it as it holds a small file. Rejects, saying why,
   *  if the file can't be read or is no longer the one that was opened. */
  #readAll(tab) {
    if (tab.bytes) return Promise.resolve(tab.bytes);
    if (tab.gone) return Promise.reject(new Error(tab.gone));
    if (!tab.reading) tab.readingAll((async () => {
      const from = tab.headCopy?.length || 0;
      let res;
      try {
        res = await fetchFor(tab.url, { headers: { "X-Pdfm-Token": this.token, Range: `bytes=${from}-${tab.size - 1}` } }, WHOLE_MS);
      } catch (e) { throw new Error("the file couldn't be read: " + (e.message || e)); }
      const bad = await pieceError(res, tab);
      if (bad) { if (bad.gone) tab.fileGone(bad.message); throw bad; }
      let rest;
      try { rest = new Uint8Array(await res.arrayBuffer()); } catch (e) { throw new Error("the file couldn't be read: " + (e.message || e)); }
      if (from + rest.length !== tab.size) throw new Error("the file couldn't be read in full");
      const all = new Uint8Array(tab.size);
      if (from) all.set(tab.headCopy);
      all.set(rest, from);
      if (tab.source) { tab.source.data = all; tab.source.serve(all); }
      const had = tab.bytes;
      tab.readInFull(all);
      if (!had && tab === this.activeTab && !this.bytes) this.bytes = all;
      return tab.bytes;
    })());
    const reading = tab.reading;
    reading.catch(() => { if (tab.reading === reading) tab.readingAll(null); });
    return reading;
  }

  /** The shown document with its unsaved annotations and form entries. */
  async _savedDocument(tab) {
    if (this.pieces?.poisoned) throw new Error(`part of the file couldn't be read (${this.pieces.failure || "it stopped answering"}); close it and open it again`);
    if (tab?.url && !tab.bytes) await this.#readAll(tab);  // (pdf.js needs every piece to write it out)
    return await bounded(this.doc.saveDocument(), WHOLE_MS, "writing the document took too long");
  }

  /** Show ``tab``'s document in the viewer. Returns false if it couldn't be
   *  opened (not a PDF, or the password prompt was cancelled). */
  async _load(tab) {
    if (tab.broken) {  // (known not to open: its state again, not another try)
      await this._closeDocument();
      this.#showBroken(tab);
      return true;
    }
    this.#busy(true);
    let pieces = null, timer = 0, tooLong = null;
    const giveUp = new Promise((_, reject) => { tooLong = reject; });
    giveUp.catch(() => {});
    const arm = () => { clearTimeout(timer); timer = setTimeout(() => tooLong(new Error("opening it took too long")), OPEN_MS); };
    try {
      await loadPdfjs();
      this.#ensureViewer();
      await this._closeDocument();
      if (!tab.bytes && !tab.head) await this.#firstPiece(tab);
      this.bytes = tab.bytes;
      this.name = tab.name;
      this.password = tab.password;
      pieces = this.pieces = tab.bytes ? null : this.#pieces(tab);
      const task = await openDocument({
        ...DOC_OPTIONS,
        ...(tab.bytes
          ? { data: tab.bytes.slice() }  // pdf.js takes ownership of the buffer; keep our copy intact
          : { range: pieces, rangeChunkSize: 1 << 20, disableAutoFetch: true, disableStream: true }),
        password: tab.password || undefined,
        enableXfa: true,
        fontExtraProperties: true,  // fonts' own names, to find the installed copy when editing text
      });
      if (pieces) pieces.task = task;
      task.onPassword = (update, reason) => {
        clearTimeout(timer);  // (the time at the password prompt doesn't count)
        this.#busy(false);
        this.#askPassword(reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD).then((pw) => {
          if (pw === null) { destroyTask(task); tooLong(Object.assign(new Error("cancelled"), { name: "PasswordException" })); return; }
          tab.unlocked(pw);
          this.password = pw;
          this.#busy(true);
          arm();
          update(pw);
        });
      };
      this.task = task;
      arm();
      // (A piece of the file that can't be had, or no answer at all: pdf.js
      // would wait for ever, so the reader stops waiting.)
      const doc = await Promise.race([task.promise, giveUp, ...(pieces ? [pieces.failed] : [])]);
      // A page tree that counts no pages (or -1), or whose first page isn't
      // there: shown as a damaged file, which Repair mends, not a viewer
      // that fails on its own.
      const unusable = () => Object.assign(new Error("its first page can't be found"), { name: "InvalidPDFException" });
      if (!(doc.numPages >= 1)) throw unusable();
      try { await Promise.race([doc.getPage(1), giveUp]); } catch (e) { if (e && e.message === "opening it took too long") throw e; throw unusable(); }
      clearTimeout(timer);
      this.doc = doc;
      this.activeTab = tab;
      doc.annotationStorage.onSetModified = () => tab.annotationChanged();
      // (Undo can bring it back to how it was opened or saved: see #compareAnnotations.)
      doc.annotationStorage.onResetModified = () => this.#compareAnnotations(tab);
      tab.loaded(doc);
      this.pdfViewer.setDocument(doc);
      this.linkService.setDocument(doc, null);
      // (The viewer hands the document to its form scripting itself once the
      // first page is drawn. Handing it over here too made two at once: the
      // second could ask a document closed meanwhile for its fields, e.g.
      // when a page was turned just after switching tabs.)
      // (The viewer hands the document to the find controller itself once the
      // first page is drawn; doing it here too would reset a search started
      // when the pages appear, e.g. from the folder search.)
      this.empty.hidden = true;
      this.broken.hidden = true;
      for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
        this.zoomInput, this.zoomMenuBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = false;
      this.pageCount.textContent = `of ${doc.numPages}`;
      this.root.classList.add("reading");
      this.#applyPanel();
      this.#setSidebar(store.get("pdfm-reader-sidebar", false));
      this.thumbs = null;  // made when the Pages panel shows them (#showThumbs)
      this.pickedPages = new Set();
      this.#renderPageBar();
      this.#buildOutline();
      this._listComments();
      this._loadMeasures();
      this._loadLayers();
      this.sigbar.hidden = true;
      this.formbar.hidden = true;
      this.#whenShown(() => { this.#checkSignatures(); this.#checkForm(); });
      // The rest of a file read in pieces, as soon as the first page is on
      // screen (not before: it would slow that down). See HOLD_BYTES.
      if (tab.url && !tab.bytes && tab.size <= HOLD_BYTES) this.#whenShown(() => this.#readAll(tab).catch(() => {}), { now: true });
      doc.getPageLabels().then((labels) => {
        if (doc !== this.doc || !labels) return;
        // Only use labels that differ from plain page numbers.
        if (labels.some((l, i) => l !== String(i + 1))) { this.labels = labels; this.pdfViewer.setPageLabels(labels); this.#updatePage(); }
      }).catch(() => {});
      return true;
    } catch (e) {
      clearTimeout(timer);
      await this._closeDocument();
      const why = pieces?.failure || (e && e.message) || String(e);
      if (tab.gone && tab.shown && this.tabs.includes(tab)) {
        // A tab left in the background whose file went away or changed: say so; it can be closed.
        this.toast(`Can't show “${tab.name}” again: ${tab.gone}.`, 15000, ["Close it", () => this.closeTab(tab)]);
      } else if (pieces?.failure) this.toast(`Could not open “${tab.name}”: ${why}`, 9000);
      else if (!tab.shown && this.tabs.includes(tab) && e && DAMAGED.has(e.name)) {
        // Damaged, or not a PDF at all: a state of its own that stays, with Repair.
        tab.broke();
        if (tab.fileId) {  // Recent says so, and doesn't put it first
          fetch("/api/recent/failed", { method: "POST", headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/json" },
            body: JSON.stringify({ id: tab.fileId }) }).catch(() => {});
        }
        this.#showBroken(tab);
        this.brokenRepair.hidden ? this.brokenClose.focus() : this.brokenRepair.focus();
        return true;
      } else if (!(e && e.name === "PasswordException")) {  // not just cancelled at the password prompt
        this.toast(e && e.name === "InvalidPDFException" ? `“${tab.name}” isn't a valid PDF.` : "Could not open the PDF: " + why);
      }
      return false;
    } finally {
      clearTimeout(timer);
      this.#busy(false);
    }
  }

  #ensureViewer() {
    if (this.pdfViewer) return;
    const eventBus = new viewer.EventBus();
    this.eventBus = eventBus;
    this.linkService = new viewer.PDFLinkService({ eventBus, externalLinkTarget: viewer.LinkTarget.BLANK, externalLinkRel: "noopener noreferrer nofollow" });
    this.findController = new viewer.PDFFindController({ eventBus, linkService: this.linkService });
    // Form scripts (number and date formats, totals, checks — Acrobat's AF
    // functions) run in pdf.js's sandbox, as in Firefox.
    this.scripting = new viewer.PDFScriptingManager({ eventBus, sandboxBundleSrc: BASE + "build/pdf.sandbox.min.mjs",
      wasmUrl: BASE + "wasm/" });
    // The viewer hands the scripting manager each document itself, without
    // waiting for the answer: one closed meanwhile (another tab shown, a
    // change reloading it) would leave a rejection nobody catches.
    const setScripted = this.scripting.setDocument.bind(this.scripting);
    this.scripting.setDocument = (doc) => setScripted(doc).catch(() => {});
    this.pdfViewer = new viewer.PDFViewer({
      container: this.container, viewer: this.viewerEl, eventBus,
      linkService: this.linkService, findController: this.findController, scriptingManager: this.scripting,
      annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS,
      annotationEditorMode: pdfjs.AnnotationEditorType.NONE,
      annotationEditorHighlightColors: HIGHLIGHT_COLORS,
      imageResourcesPath: BASE + "web/images/",
    });
    this.linkService.setViewer(this.pdfViewer);
    this.scripting.setViewer(this.pdfViewer);
    // The window (or the sidebar) changes size: a zoom that fits the page
    // fits it again, as a reader should. What's watched is the area around
    // the pages, not the scrolling box itself: that one narrows when its
    // scrollbar appears (Windows), which a new fit can bring or take away,
    // and the fitting would go round and round.
    // A bar opening or closing above the pages (find, annotate, scale) only
    // changes the height a little: that's not a reason to zoom.
    let refit = 0, lastW = 0, lastH = 0;
    new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (!width || !height) return;  // hidden (another page of the app is shown): nothing to fit to
      if (Math.abs(width - lastW) < 4 && Math.abs(height - lastH) < 100) return;
      lastW = width;
      lastH = height;
      cancelAnimationFrame(refit);
      refit = requestAnimationFrame(() => {
        const value = this.pdfViewer.currentScaleValue;
        if (this.doc && ["auto", "page-width", "page-fit", "page-height"].includes(value)) this.pdfViewer.currentScaleValue = value;
      });
    }).observe(this.main);
    eventBus.on("annotationeditoruimanager", ({ uiManager }) => { this.uiManager = uiManager; this._applyAnnotParams(); });
    // pdf.js asks to switch tools itself, e.g. after a picture is pasted, or
    // for a click (double-click on one in the file) on an annotation with no
    // tool on: that one is then picked, to move, resize or delete.
    eventBus.on("switchannotationeditormode", ({ mode, editId = null, mustEnterInEditMode = false, editComment = false }) =>
      this._setEditMode(this._modeName(mode), false, editId ? { editId, mustEnterInEditMode, editComment } : null));
    eventBus.on("annotationeditormodechanged", ({ mode }) => this._showEditMode(this._modeName(mode)));
    eventBus.on("editingstateschanged", ({ details }) => {
      this.annotUndo = !!details.hasSomethingToUndo;
      this.annotRedo = !!details.hasSomethingToRedo;
      this.#compareAnnotations();
      this._refresh({ notify: false });
      this.deleteBtn.disabled = !details.hasSelectedEditor;
    });

    eventBus.on("pagesinit", () => {
      const saved = store.get("pdfm-pos:" + this._key(), null);
      this.pdfViewer.currentScaleValue = saved?.scale || "auto";
      if (saved?.page > 1 && saved.page <= this.doc.numPages) this.pdfViewer.currentPageNumber = saved.page;
      if (saved?.rotation) this.pdfViewer.pagesRotation = saved.rotation;
      const goto = this.activeTab?.arrived();
      if (goto) this._goto(goto);
      const tab = this.activeTab, tool = tab?.restored();
      if (tool) {
        // (Once a page's editing layer is drawn: before that, pdf.js's viewer
        // still announces the tool it had, and could leave its own unchanged.)
        let done = false, timer = 0;
        const restore = () => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          eventBus.off("annotationeditorlayerrendered", restore);
          setTimeout(() => {
            if (this.activeTab !== tab || !this.doc || !this.uiManager) return;
            this.openAnnotate();
            if (tool in pdfjs.AnnotationEditorType && tool !== "NONE") this._setEditMode(tool);
          }, 0);
        };
        eventBus.on("annotationeditorlayerrendered", restore);
        timer = setTimeout(restore, 5000);
      }
      this.#updatePage();
      this.#showThumbs();
      this.container.focus({ preventScroll: true, focusVisible: false });  // (no ring when a file opens)
    });
    // pdf.js makes each page's text a Tab stop; the page view is one already (text still selects).
    eventBus.on("textlayerrendered", ({ source }) => { const layer = source?.textLayer?.div || source?.div?.querySelector(".textLayer"); if (layer) layer.tabIndex = -1; });
    eventBus.on("pagechanging", ({ pageNumber }) => { this.#updatePage(); this.#remember(); this._refreshScale(); if (this.measureMode) this._geometry(pageNumber); });
    eventBus.on("pagesloaded", () => { this.#whenShown(() => this._refreshScale()); this.#sizeThumbs(); });
    eventBus.on("annotationlayerrendered", ({ source }) => this.#nameFields(source?.div?.querySelector(".annotationLayer")));
    eventBus.on("pagerendered", ({ pageNumber }) => {
      this._drawMeasures(pageNumber);
      if (this.markupMode === "IMAGES" || this.markupMode === "LINKS") this._drawObjectBoxes(pageNumber);
      this._drawRedactions(pageNumber);
      if (this.overlay) this.overlay.draw(this.pdfViewer.getPageView(pageNumber - 1), pageNumber).catch(() => {});
    });
    eventBus.on("scalechanging", ({ scale, presetValue }) => {
      const value = presetValue || String(scale);
      const preset = ZOOM_PRESETS.find(([v]) => v === value || (!presetValue && Math.abs(parseFloat(v) - scale) < 0.001));
      if (!presetValue && Math.abs(scale - this._trueZoom()) < 0.001) this.zoomPreset = "true-size";
      else this.zoomPreset = preset ? preset[0] : "custom";
      this.#showZoom(scale);
      this._syncViewAt();
      this.#remember();
      requestAnimationFrame(() => this._drawAllMeasures());
    });
    eventBus.on("rotationchanging", () => { this.#remember(); requestAnimationFrame(() => this._drawAllMeasures()); });
    eventBus.on("updatefindmatchescount", ({ matchesCount }) => this._findResult(null, matchesCount));
    eventBus.on("updatefindcontrolstate", ({ state, matchesCount }) => this._findResult(state, matchesCount));
  }

  async _closeDocument() {
    this.gaveWay = null;  // (no bar comes back for a document that is going)
    this._putAwayMeasures();  // (its list of measurements goes with it)
    this._closeLayers();
    this.closeFind();
    await this.closeCompare();
    this.closeAnnotate();
    this.measureTool(null);
    this.uiManager = null;
    this.annotUndo = this.annotRedo = false;
    this.labels = null;
    for (const stop of this.asking || []) { stop.closed = true; stop.abort(); }
    this.thumbs = null;
    this.thumbsList.replaceChildren();
    this.outlineList.replaceChildren();
    const doc = this.doc, task = this.task, pieces = this.pieces;
    this.doc = null;
    this.task = null;
    this.pieces = null;
    // A file read in pieces: what's still on its way is given up, and if
    // pdf.js won't let go of the document soon, the reader doesn't wait.
    const wait = pieces && (pieces.poisoned || pieces.waiting()) ? 2000 : 10000;
    pieces?.giveUp();
    this._refresh({ notify: false });
    if (doc) {
      closeOff(doc);
      this.pdfViewer.setDocument(null);
      this.linkService.setDocument(null);
      this.findController.setDocument(null);
      // (The viewer lets go of the form scripting too; it finishes before the
      // document goes, so it never asks a destroyed one for anything.)
      const scripts = this.scripting?.destroyPromise;
      if (scripts) await Promise.race([scripts, new Promise((r) => setTimeout(r, 1500))]).catch(() => {});
      await destroyTask(doc.loadingTask || task, wait);  // pdf.js frees a document through its task
    } else if (task) {
      await destroyTask(task, wait);  // e.g. cancelled at the password prompt: wait for it to be gone
    }
  }

  _key() { return this.doc ? this.doc.fingerprints[0] : ""; }
  #remember() {
    if (!this.doc) return;
    store.set("pdfm-pos:" + this._key(), {
      page: this.pdfViewer.currentPageNumber, scale: this.pdfViewer.currentScaleValue, rotation: this.pdfViewer.pagesRotation,
    });
  }

  #busy(on) { this.loading.hidden = !on; }

  // ---- navigation -----------------------------------------------------------
  #updatePage() {
    if (!this.doc) return;
    const v = this.pdfViewer, n = v.currentPageNumber || 1;
    const total = v.pagesCount || this.doc.numPages;  // before layout, pagesCount is 0
    this.pageInput.value = this.labels ? v.currentPageLabel || n : n;
    this.pageCount.textContent = this.labels ? `(${n} of ${total})` : `of ${total}`;
    this.prevBtn.disabled = n <= 1;
    this.nextBtn.disabled = n >= total;
    const t = this.thumbs;
    if (!t) return;
    if (t.current !== n) {
      t.rows.get(t.current)?.classList.remove("current");
      t.rows.get(n)?.classList.add("current");
      t.current = n;
    }
    this.#revealThumb(n);
  }

  #goToTypedPage() {
    const v = this.pdfViewer, text = this.pageInput.value.trim();
    if (this.labels && this.labels.includes(text)) { v.currentPageLabel = text; return; }
    const n = parseInt(text, 10);
    if (n >= 1 && n <= v.pagesCount) v.currentPageNumber = n;
    else this.#updatePage();
  }

  rotate(delta) {
    // Just after a tab was picked, the one before is still showing: turn the picked one once it's there.
    if (this.switching) { this._serial(() => this.rotate(delta)); return; }
    if (!this.doc) return;
    // (Pages not laid out yet: after they are, and after the turn remembered for the file.)
    if (!this.pdfViewer.pagesCount) { const doc = this.doc; this.eventBus.on("pagesinit", () => { if (this.doc === doc) this.rotate(delta); }, { once: true }); return; }
    this.pdfViewer.pagesRotation = (this.pdfViewer.pagesRotation + delta + 360) % 360;
    this.#rebuildThumbsSoon();
  }

  toggleSpread() {
    const { SpreadMode } = viewer;
    this.pdfViewer.spreadMode = this.pdfViewer.spreadMode === SpreadMode.NONE ? SpreadMode.ODD : SpreadMode.NONE;
  }
  toggleHand() { this.container.classList.toggle("hand"); }
  toggleDarkPages() {
    const on = this.container.classList.toggle("dark");
    store.set("pdfm-reader-dark", on);
  }
  toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (this.root.requestFullscreen) this.root.requestFullscreen().catch(() => this.toast("Full screen isn't available here."));
    else this.toast("Full screen isn't available here.");
  }

  // ---- the file panel (its content comes from the app) ----------------------------
  /** In a window 1100 px wide or more the panel sits beside the pages and
   *  stays open or closed as it was left ("pdfm-file-panel"); narrower, it
   *  comes over the pages when asked for and goes again. */
  #panelDocked() { return !this.wideQuery || this.wideQuery.matches; }
  get panelOpen() { return this.#panelDocked() ? store.get("pdfm-file-panel", true) !== false : !!this.overlayOpen; }
  /** While the annotate bar or the find bar is open the file panel steps
   *  aside (to its button), so the page has the room; it comes back when
   *  they close. Its open/closed choice isn't changed. Opened again by hand
   *  meanwhile, it stays. */
  _stepAside() {
    const busy = !this.annotbar.hidden || !this.findbar.hidden;
    if (!busy) this.panelKept = false;
    const aside = busy && !this.panelKept;
    if (aside === !!this.panelAside) return;
    const had = aside && this.side.contains(document.activeElement);
    this.panelAside = aside;
    this.#applyPanel();
    if (had) this.container.focus({ preventScroll: true });
  }

  #applyPanel() {
    const docked = this.#panelDocked(), open = this.panelOpen && !this.panelAside;
    this.side.hidden = !open;
    this.side.classList.toggle("overlay", !docked);
    this.sideScrim.hidden = !(open && !docked);
    this.panelBtn.setAttribute("aria-expanded", String(open));
  }
  /** Open or close the panel; ``focus``: move the focus into it (opening) or
   *  back to the page (closing). */
  setPanel(open, { focus = false } = {}) {
    const had = this.side.contains(document.activeElement);
    if (open && this.panelAside) { this.panelAside = false; this.panelKept = true; }  // asked for by hand: it stays
    else if (!open && this.panelAside) { this.#applyPanel(); return; }  // (already out of the way)
    if (this.#panelDocked()) store.set("pdfm-file-panel", !!open);
    else this.overlayOpen = !!open;
    this.#applyPanel();
    if (open && focus) (this.side.querySelector("button, [href], input, select, [tabindex]:not([tabindex='-1'])") || this.side).focus?.();
    if (!open && (focus || had)) (this.doc ? this.panelBtn : this.container).focus({ preventScroll: true });
  }
  togglePanel() { this.setPanel(!(this.panelOpen && !this.panelAside), { focus: !this.#panelDocked() }); }

  // ---- sidebar: thumbnails and bookmarks ----------------------------------------
  toggleSidebar() { this.#setSidebar(this.sidebar.hidden); store.set("pdfm-reader-sidebar", !this.sidebar.hidden); }
  #setSidebar(open) {
    this.sidebar.hidden = !open;
    this.sidebarBtn.setAttribute("aria-pressed", String(open));
    if (open) { this.#updatePage(); this.#showThumbs(); }
  }
  #showTab(which) {
    for (const [name, tab, panel] of [["thumbs", this.thumbsTab, this.thumbsPanel], ["outline", this.outlineTab, this.outlinePanel],
      ["comments", this.commentsTab, this.commentsPanel], ["layers", this.layersTab, this.layersPanel]]) {
      tab.setAttribute("aria-selected", String(name === which));
      panel.hidden = name !== which;
    }
    if (which === "comments") this._listComments();
    if (which === "thumbs") this.#showThumbs();
  }

  // Thumbnails are made when the Pages panel first shows them, and only the
  // rows near what the panel shows exist: a document of 3000 pages costs what
  // one of 30 does. Every page still has its place in the list (worked out
  // from the pages' sizes), so the list scrolls as if all of them were there.
  #thumbsVisible() { return !!this.doc && !this.sidebar.hidden && !this.thumbsPanel.hidden && this.pdfViewer?.pagesCount > 0; }

  /** The Pages panel is shown (or its document just got its pages): make the
   *  thumbnails if they aren't there yet. */
  #showThumbs() {
    if (!this.#thumbsVisible()) return;
    if (!this.thumbs || this.thumbs.doc !== this.doc) this.#buildThumbs();
    else this.#layoutThumbs();
    this.#revealThumb();
  }

  #buildThumbs() {
    const doc = this.doc, count = doc.numPages;
    this.thumbsList.replaceChildren();
    // heights[n]: how tall page n's row is; tops[n]: where it starts in the list.
    this.thumbs = { doc, count, heights: new Float64Array(count + 2), tops: new Float64Array(count + 2), rows: new Map(),
      canvases: new Map(), queue: [], busy: false, held: null, current: this.pdfViewer.currentPageNumber || 1 };
    this.#sizeThumbs();
  }

  /** Every row's height from its page's size (as far as the viewer knows the
   *  sizes: all of them once "pagesloaded"), then the rows in their places. */
  #sizeThumbs() {
    const t = this.thumbs;
    if (!t || t.doc !== this.doc) return;
    for (let n = 1; n <= t.count; n++) {
      const vp = this.pdfViewer.getPageView(n - 1)?.viewport;
      t.heights[n] = Math.round(128 * (vp ? vp.height / vp.width : 1.4142)) + THUMB_EXTRA;
    }
    this.#placeThumbs();
    this.#layoutThumbs();
  }

  #placeThumbs() {
    const t = this.thumbs;
    for (let n = 1; n <= t.count; n++) t.tops[n + 1] = t.tops[n] + t.heights[n];
    this.thumbsList.style.height = t.tops[t.count + 1] + "px";
    for (const [n, li] of t.rows) li.style.top = THUMB_PAD + t.tops[n] + "px";
  }

  /** Where the list starts in what its panel scrolls. */
  #thumbsTop() {
    const panel = this.thumbsPanel;
    return this.thumbsList.getBoundingClientRect().top - panel.getBoundingClientRect().top + panel.scrollTop;
  }

  /** The rows the panel shows now (and a screenful around them) exist; the others don't. */
  #layoutThumbs() {
    const t = this.thumbs;
    if (!t || !this.#thumbsVisible()) return;
    const panel = this.thumbsPanel, around = Math.max(400, panel.clientHeight);
    const from = panel.scrollTop - this.#thumbsTop() - THUMB_PAD - around, to = from + panel.clientHeight + 2 * around;
    const rowAt = (y) => {  // the last row that starts at or above y
      let lo = 1, hi = t.count;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (t.tops[mid] <= y) lo = mid; else hi = mid - 1; }
      return lo;
    };
    const first = rowAt(from), last = rowAt(to);
    for (const [n, li] of t.rows) {
      // (A row being dragged, or holding the keyboard focus, stays.)
      if ((n < first || n > last) && li !== t.held && !li.contains(document.activeElement)) { li.remove(); t.rows.delete(n); }
    }
    for (let n = first; n <= last; n++) if (!t.rows.has(n)) this.#addThumb(n);
    if (t.queue.length && !t.busy) this.#drainThumbs();
  }

  #addThumb(n) {
    const t = this.thumbs, list = this.thumbsList;
    const canvas = t.canvases.get(n);
    const box = el("div", { class: "rd-thumb-img", style: `height:${t.heights[n] - THUMB_EXTRA}px` }, canvas || null);
    const actions = el("div", { class: "rd-thumb-actions" },
      el("button", { type: "button", class: "rd-thumb-act", "data-act": "rotate", title: "Turn clockwise", "aria-label": `Turn page ${n} clockwise`, html: svg("cw") }),
      el("button", { type: "button", class: "rd-thumb-act", "data-act": "delete", title: "Delete page", "aria-label": `Delete page ${n}`, html: svg("trash") }));
    const li = el("li", { class: "rd-thumb" + (n === t.current ? " current" : ""), "data-page": n, tabindex: "0", role: "option", draggable: "true",
      "aria-selected": String(this.pickedPages.has(n)), "aria-label": `Page ${n}`, "aria-posinset": n, "aria-setsize": t.count,
      style: `top:${THUMB_PAD + t.tops[n]}px` }, box, actions, el("span", {}, String(n)));
    t.rows.set(n, li);
    // In page order, for the keyboard and for screen readers.
    let before = null;
    if (list.lastElementChild && +list.lastElementChild.dataset.page > n) {
      for (const other of list.children) if (+other.dataset.page > n) { before = other; break; }
    }
    list.insertBefore(li, before);
    if (!canvas) t.queue.push(n);
  }

  /** Bring page ``n``'s row (the current page's) into what the panel shows. */
  #revealThumb(n = this.pdfViewer?.currentPageNumber || 1) {
    const t = this.thumbs;
    if (!t || !this.#thumbsVisible() || n < 1 || n > t.count) return;
    const panel = this.thumbsPanel, top = this.#thumbsTop() + THUMB_PAD + t.tops[n], bottom = top + t.heights[n];
    if (top < panel.scrollTop || bottom > panel.scrollTop + panel.clientHeight) panel.scrollTop = top - panel.clientHeight / 3;
    this.#layoutThumbs();
  }

  #focusThumb(n) {
    const t = this.thumbs;
    if (!t || n < 1 || n > t.count) return;
    this.#revealThumb(n);
    t.rows.get(n)?.focus();
  }

  // ---- thumbnails: pick, drag to move, drop a PDF to insert ---------------------------------
  #select(pages, anchor = pages.at(-1)) {
    this.pickedPages = new Set(pages);
    this.selectAnchor = anchor;
    for (const [n, li] of this.thumbs?.rows || []) li.setAttribute("aria-selected", String(this.pickedPages.has(n)));
    this.#renderPageBar();
  }

  #renderPageBar() {
    const pages = [...this.pickedPages].sort((a, b) => a - b);
    const b = (icon, label, run, title = label) => {
      const btn = el("button", { type: "button", class: "rd-btn", title, "aria-label": title });
      btn.innerHTML = svg(icon) + (label ? `<span>${label}</span>` : "");
      btn.addEventListener("click", run);
      return btn;
    };
    if (pages.length > 1) {
      this.pageBar.replaceChildren(
        el("span", { class: "rd-pagebar-count" }, `${pages.length} pages`),
        b("cw", "", () => this.rotatePages(pages, 90), "Turn them clockwise"),
        b("copypage", "", () => this.duplicatePages(pages), "Duplicate them"),
        b("save", "", () => this.extractPages(pages), "Extract them as a new PDF"),
        b("trash", "", () => this.deletePages(pages), "Delete them"),
        b("x", "", () => this.#select([]), "Clear the selection"));
    } else {
      const after = pages[0] || this.pdfViewer?.currentPageNumber || 0;
      this.pageBar.replaceChildren(b("plus", "Insert PDF", () => this.insertPdf(after), "Insert a PDF's pages after the selected page"),
        el("span", { class: "rd-pagebar-hint" }, "Drag to reorder"));
    }
  }

  #wireThumbs() {
    const list = this.thumbsList;
    const pageOf = (e) => Number(e.target.closest?.(".rd-thumb")?.dataset.page) || 0;
    list.addEventListener("click", (e) => {
      const n = pageOf(e);
      if (!n) return;
      const act = e.target.closest(".rd-thumb-act")?.dataset.act;
      if (act === "rotate") return this.rotatePages(this.pickedPages.has(n) ? [...this.pickedPages] : [n], 90);
      if (act === "delete") return this.deletePages(this.pickedPages.has(n) && this.pickedPages.size > 1 ? [...this.pickedPages] : [n]);
      if (e.shiftKey && this.selectAnchor) {
        const [a, z] = [Math.min(this.selectAnchor, n), Math.max(this.selectAnchor, n)];
        this.#select(Array.from({ length: z - a + 1 }, (_, i) => a + i), this.selectAnchor);
      } else if (e.ctrlKey || e.metaKey) {
        const next = new Set(this.pickedPages);
        if (next.has(n)) next.delete(n); else next.add(n);
        this.#select([...next], n);
      } else {
        this.#select([n]);
        this.pdfViewer.currentPageNumber = n;
      }
    });
    list.addEventListener("keydown", (e) => {
      const n = pageOf(e);
      if (!n) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.#select([n]); this.pdfViewer.currentPageNumber = n; }
      if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); this.deletePages(this.pickedPages.has(n) ? [...this.pickedPages] : [n]); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        this.#focusThumb(n + (e.key === "ArrowDown" ? 1 : -1));
      }
    });
    // Dragging pages: a line shows where they'll go.
    let marker = null;
    const clear = () => { if (marker) marker.classList.remove("drop-before", "drop-after"); marker = null; };
    const spot = (e) => {
      const li = e.target.closest?.(".rd-thumb");
      if (!li) {  // below the last page: after it
        const t = this.thumbs;
        return t ? { li: t.rows.get(t.count) || null, before: t.count + 1, after: true } : null;
      }
      const r = li.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2;
      return { li, before: Number(li.dataset.page) + (after ? 1 : 0), after };
    };
    const isFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
    list.addEventListener("dragstart", (e) => {
      const n = pageOf(e);
      if (!n) return;
      if (!this.pickedPages.has(n)) this.#select([n]);
      e.dataTransfer.setData("application/x-zpdf-pages", JSON.stringify([...this.pickedPages]));
      e.dataTransfer.effectAllowed = "move";
      if (this.thumbs) this.thumbs.held = e.target.closest(".rd-thumb");  // stays while it's dragged, wherever the list scrolls
    });
    list.addEventListener("dragover", (e) => {
      const ours = [...(e.dataTransfer?.types || [])].includes("application/x-zpdf-pages");
      if (!ours && !isFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = ours ? "move" : "copy";
      const where = spot(e);
      if (!where) return;
      if (marker !== where.li) clear();
      marker = where.li;
      marker?.classList.toggle("drop-after", where.after);
      marker?.classList.toggle("drop-before", !where.after);
    });
    list.addEventListener("dragleave", (e) => { if (!list.contains(e.relatedTarget)) clear(); });
    const release = () => { if (this.thumbs?.held) { this.thumbs.held = null; this.#layoutThumbs(); } };
    list.addEventListener("dragend", () => { clear(); release(); });
    this.thumbsPanel.addEventListener("scroll", () => this.#layoutThumbs(), { passive: true });
    new ResizeObserver(() => this.#layoutThumbs()).observe(this.thumbsPanel);
    list.addEventListener("drop", (e) => {
      const where = spot(e);
      clear();
      release();
      const moving = e.dataTransfer.getData("application/x-zpdf-pages");
      if (moving) {
        e.preventDefault(); e.stopPropagation();
        if (where) this.movePages(JSON.parse(moving), where.before);
        return;
      }
      const pdf = [...(e.dataTransfer.files || [])].find((f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name));
      if (pdf && where) {  // a PDF dropped among the pages goes in there
        e.preventDefault(); e.stopPropagation();
        this.insertPdf(where.before - 1, pdf);
      }
    });
  }

  /** Draw the thumbnails of the rows that are waiting, one after the other. */
  async #drainThumbs() {
    const t = this.thumbs, doc = this.doc;
    t.busy = true;
    while (t.queue.length && t === this.thumbs && doc === this.doc) {
      const n = t.queue.shift();
      if (!t.rows.has(n) || t.canvases.has(n)) continue;  // scrolled away meanwhile: drawn when it's back
      try {
        const page = await doc.getPage(n);
        const rotation = (page.rotate + this.pdfViewer.pagesRotation) % 360;
        const vp = page.getViewport({ scale: 1, rotation });
        const scale = (128 * (window.devicePixelRatio || 1)) / vp.width;
        const viewport = page.getViewport({ scale, rotation });
        const canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
        await page.render({ canvas, viewport, annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS,
          optionalContentConfigPromise: this._layerConfigPromise() }).promise;  // (the layers shown)
        if (t !== this.thumbs || doc !== this.doc) break;
        // Drawn thumbnails are kept for when their row comes back; not all of a long document's.
        t.canvases.set(n, canvas);
        for (const old of t.canvases.keys()) {
          if (t.canvases.size <= THUMBS_KEPT) break;
          if (!t.rows.has(old)) t.canvases.delete(old);
        }
        const height = Math.round(128 * viewport.height / viewport.width);
        const box = t.rows.get(n)?.firstChild;
        if (box) { box.style.height = height + "px"; box.replaceChildren(canvas); }
        if (height + THUMB_EXTRA !== t.heights[n]) {  // not the size its place was kept for: the rows after it move
          const grew = height + THUMB_EXTRA - t.heights[n], panel = this.thumbsPanel;
          const above = this.#thumbsTop() + THUMB_PAD + t.tops[n] < panel.scrollTop;
          t.heights[n] = height + THUMB_EXTRA;
          this.#placeThumbs();
          if (above) panel.scrollTop += grew;  // (what the panel shows stays where it is)
        }
      } catch (e) { /* skip pages that fail to render */ }
    }
    t.busy = false;
  }

  /** Draw the thumbnails again where they are (the layers shown changed). */
  _redrawThumbs() {
    const t = this.thumbs;
    if (!t || t.doc !== this.doc) return;
    t.canvases.clear();
    for (const n of t.rows.keys()) if (!t.queue.includes(n)) t.queue.push(n);
    if (!t.busy) this.#drainThumbs();
  }

  /** Show the sidebar's tab ``which`` ("thumbs", "outline", "comments", "layers"). */
  _showSidebarTab(which) { this.#showTab(which); }

  #rebuildThumbsSoon() {
    clearTimeout(this.thumbTimer);
    this.thumbTimer = setTimeout(() => {
      if (!this.doc) return;
      this.thumbs = null;
      this.thumbsList.replaceChildren();
      this.pickedPages = new Set();
      this.#renderPageBar();
      this.#showThumbs();
    }, 250);
  }

  async #buildOutline() {
    const doc = this.doc;
    const outline = await doc.getOutline().catch(() => null);
    if (doc !== this.doc) return;
    this.outlineTab.disabled = !outline || !outline.length;
    if (!outline || !outline.length) {
      this.outlineList.replaceChildren();
      this.outlinePanel.replaceChildren(el("p", { class: "rd-empty-note" }, "This document has no bookmarks."));
      if (!this.outlinePanel.hidden) this.#showTab("thumbs");
      return;
    }
    this.outlinePanel.replaceChildren(this.outlineList);
    const build = (items, depth) => items.map((item) => {
      const li = el("li", { class: [item.bold ? "bold" : "", item.italic ? "italic" : ""].join(" ").trim() || null });
      const link = el("a", { href: "#", title: item.title }, item.title || "(untitled)");
      link.addEventListener("click", (e) => {
        e.preventDefault();
        if (item.dest) this.linkService.goToDestination(item.dest);
        else if (item.url) window.open(item.url, "_blank", "noopener,noreferrer");
        if (window.innerWidth <= 760) this.#setSidebar(false);
      });
      const row = el("div", { class: "row" });
      if (item.items && item.items.length) {
        const open = depth === 0 && item.items.length <= 12;
        const children = el("ul", { hidden: !open }, ...build(item.items, depth + 1));
        const twisty = el("button", { class: "twisty", type: "button", "aria-expanded": String(open), "aria-label": "Expand", html: `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON.twisty}</svg>` });
        twisty.addEventListener("click", () => {
          const expanded = twisty.getAttribute("aria-expanded") !== "true";
          twisty.setAttribute("aria-expanded", String(expanded));
          children.hidden = !expanded;
        });
        row.append(twisty, link);
        li.append(row, children);
      } else {
        row.append(el("span", { class: "spacer" }), link);
        li.append(row);
      }
      return li;
    });
    this.outlineList.replaceChildren(...build(outline, 0));
    // Long documents with bookmarks open on the bookmarks tab.
    // (The Comments tab stays open while comments are being worked on.)
    if (doc.numPages >= 20 && this.commentsPanel.hidden) this.#showTab("outline");
  }

  /** Show page ``page`` and search for ``find`` from there; ``scale`` and
   *  ``top``/``left`` (scroll position) bring back an earlier view. */
  _goto({ page, find, scale, top, left } = {}) {
    if (scale) this.pdfViewer.currentScaleValue = scale;
    if (page && page <= this.pdfViewer.pagesCount) this.pdfViewer.currentPageNumber = page;
    if (top != null) { this.container.scrollTop = top; this.container.scrollLeft = left || 0; }
    if (find) {
      this.openFind();
      this.findInput.value = find;
      this._find("");
    }
  }

  // ---- digital ID signatures (certsign.py) --------------------------------------------
  /** Sign the document with a digital ID, its signature shown in ``rect`` on page ``n``. */
  async _signWithId(n, rect) {
    const pfx = el("input", { type: "file", accept: ".pfx,.p12,application/x-pkcs12", "aria-label": "Digital ID file" });
    const password = el("input", { type: "password", autocomplete: "off", "aria-label": "Digital ID password" });
    const reason = el("input", { type: "text", placeholder: "e.g. I approve this document", "aria-label": "Reason" });
    const location = el("input", { type: "text", placeholder: "e.g. Vancouver", "aria-label": "Location" });
    const stamp = el("input", { type: "text", placeholder: "none (e.g. http://timestamp.digicert.com)", "aria-label": "Timestamp server",
      value: store.get("pdfm-timestamp-url", "") });
    const answer = await this._dialog("Digital ID", [
      el("p", {}, "Your Digital ID is a .pfx or .p12 file with your certificate. The signature seals the document: a change after signing shows up when it's checked."),
      el("label", { class: "rd-field" }, "Digital ID file", pfx), el("label", { class: "rd-field" }, "Its password", password),
      el("label", { class: "rd-field" }, "Reason (optional)", reason), el("label", { class: "rd-field" }, "Location (optional)", location),
      el("label", { class: "rd-field" }, "Timestamp server (optional)", stamp)],
      [["cancel", "Cancel"], ["ok", "Sign", true]],
      { ready: () => { if (pfx.files[0]) return true; pfx.focus(); return false; } });  // (Enter before a digital ID is chosen: to the file box, nothing lost)
    if (answer !== "ok") return;
    const file = pfx.files[0];
    if (!file) { this.toast("Choose your Digital ID file (.pfx or .p12)."); return; }
    store.set("pdfm-timestamp-url", stamp.value.trim());
    const tab = this.activeTab, version = tab?.version || 0;
    await this._changeDocument((bytes, t) => {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      body.append("pfx", file, file.name);
      for (const [k, v] of Object.entries({ pfx_password: password.value, password: t.password || "", page: String(n), box: JSON.stringify(rect),
        reason: reason.value.trim(), location: location.value.trim(), timestamp_url: stamp.value.trim() })) body.append(k, v);
      return fetch("/api/sign/cert", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    }, { failure: "Could not sign: ", signing: true });
    if ((tab?.version || 0) !== version) {
      if (this.markupMode === "CERTSIGN") this._setEditMode("NONE");
      tab.signedWarned = false;
      this.toast("Signed. Save it now: anything changed after this shows as changed after signing.", 7000, ["Save", () => this.save()]);
    }
  }

  /** What the document's signatures say: { signatures: [...] } (see certsign.verify). */
  async #verifySignatures() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return [];
    const body = await this.#about(tab);
    if (!body) return [];
    const res = await fetch("/api/sign/verify", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return (await res.json()).signatures || [];
  }

  /** What a request about ``tab``'s document sends: the id of the file on
   *  this computer while that file holds what the tab shows (nothing is
   *  uploaded then), else the document itself. null: nothing to send. */
  async #about(tab, fields = {}) {
    const body = new FormData();
    if (this.isPristine(tab)) body.append("id", tab.fileId);
    else {
      const bytes = tab === this.activeTab ? await this._fullBytes() : tab.bytes;  // (annotations not saved yet don't matter here)
      if (!bytes) return null;
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
    }
    body.append("password", tab.password || "");
    for (const [k, v] of Object.entries(fields)) body.append(k, v);
    return body;
  }

  /** A question the reader asks the server on its own about the shown
   *  document. It is given up when the document is closed (the error then
   *  has ``closed``) and after ``ms``: nothing waits for an answer for ever. */
  async #ask(url, body, ms = 30000) {
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), ms);
    (this.asking ||= new Set()).add(stop);
    try {
      const res = await fetch(url, { method: "POST", body, headers: { "X-Pdfm-Token": this.token }, signal: stop.signal });
      return { ok: res.ok, data: await res.json().catch(() => ({})) };
    } catch (e) {
      e.closed = !!stop.closed;
      throw e;
    } finally {
      clearTimeout(timer);
      this.asking.delete(stop);
    }
  }

  /** Run ``fn`` once the shown document's first page is on screen and the
   *  page has a quiet moment (not at all if another document is shown by
   *  then): what isn't needed to show the file doesn't hold it up. */
  #whenShown(fn, { now = false } = {}) {
    const doc = this.doc, bus = this.eventBus;
    let timer = 0, done = false;
    const go = () => { if (doc === this.doc) fn(); };
    const shown = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      bus.off("pagerendered", shown);
      if (now) setTimeout(go, 0);  // (``now``: not waiting for a quiet moment)
      else if (window.requestIdleCallback) requestIdleCallback(go, { timeout: 1500 }); else setTimeout(go, 100);
    };
    const view = this.pdfViewer.getPageView((this.pdfViewer.currentPageNumber || 1) - 1);
    if (view?.renderingState === 3) { shown(); return; }  // (it's on screen already)
    bus.on("pagerendered", shown);
    timer = setTimeout(shown, 5000);  // (a first page that takes long to draw, or fails)
  }

  /** Resolves once the shown document's first page is on screen and the page is idle. */
  shown() { return new Promise((resolve) => { if (this.doc) this.#whenShown(resolve); else resolve(); }); }

  /** A signed document says so (and whether the signatures hold) in a bar. */
  /** Form fields' names for screen readers: pdf.js leaves its inputs
   *  unnamed (it puts the tooltip on the box around them). */
  async #nameFields(layer) {
    const doc = this.doc;
    if (!doc || !layer) return;
    if (!this.fieldNames || this.fieldNames.doc !== doc) {
      const names = new Map();
      try {
        for (const [name, list] of fieldEntries(await doc.getFieldObjects())) {
          for (const f of list) names.set(f.id, f.type === "radiobutton" && f.exportValues ? `${name}: ${f.exportValues}` : name);
        }
      } catch (e) { /* unnamed then */ }
      this.fieldNames = { doc, names };
    }
    for (const input of layer.querySelectorAll("input, select, textarea")) {
      if (input.getAttribute("aria-label") || input.labels?.length) continue;
      const box = input.closest("[data-annotation-id]");
      const label = box?.title || this.fieldNames.names.get(input.dataset.elementId || box?.dataset.annotationId);
      if (label) input.setAttribute("aria-label", label);
    }
  }

  /** A printed form with no fields to type in (lines, boxes, tick boxes on
   *  its first pages): offer to make it fillable. */
  async #checkForm() {
    const doc = this.doc, tab = this.activeTab;
    this.formbar.hidden = true;
    if (!doc || !tab || tab.formHint === "hidden" || tab.formHint === "has-fields" || tab.formHint === "checking") return;
    tab.formHint ||= "checking";
    try {
      // (Fields first: also after "Make it fillable" reloads the document.)
      if (fieldEntries(await doc.getFieldObjects()).length) { tab.formHint = "has-fields"; return; }
      if (tab.formHint === "show") { if (doc === this.doc) this.#showFormHint(tab); return; }
      // (A document that isn't a file on this computer is sent to be looked at: not a very big one.)
      if (tab.formHint !== "checking" || !this.isPristine(tab) && (!tab.bytes || tab.bytes.length > 30e6)) { tab.formHint = "none"; return; }
      const body = await this.#about(tab, { pages: "1-2" });
      const res = body && await this.#ask("/api/form/detect", body);
      if (!res?.ok) { tab.formHint = "none"; return; }
      const found = Object.values(res.data.fields || {}).flat();
      tab.formHint = found.length >= 3 ? "show" : "none";
      if (tab.formHint === "show" && doc === this.doc && tab === this.activeTab) this.#showFormHint(tab);
      if (tab.formHint === "show") this.onFormHint?.(tab);
    } catch (e) {
      tab.formHint = e.closed ? null : "none";  // closed meanwhile (another tab shown): looked at again when it's back
    }
  }

  #showFormHint(tab) {
    this.formText.textContent = "This looks like a form to fill in, but you can't type in it yet.";
    this.formbar.hidden = false;
  }

  /** Whether ``tab`` looks like a printed form with nothing to type in yet
   *  (what the "Make it fillable" bar is shown for). */
  looksLikeFlatForm(tab = this.activeTab) { return !!tab && tab.formHint === "show"; }

  /** Add the fields of a printed form, so it can be filled in here. */
  makeFillable() {
    if (!this.doc) return;
    this.formbar.hidden = true;
    return this._changeDocument((bytes, tab) => this._post("/api/form/fillable", bytes, tab), {
      failure: "Couldn't make it fillable: ",
      after: (res) => {
        const n = Number(res.headers.get("X-Pdfm-Fields")) || 0;
        if (this.activeTab) { this.activeTab.formHint = "has-fields"; this.onFormHint?.(this.activeTab); }
        this.toast(`Added ${n} place${n === 1 ? "" : "s"} to fill in: click one to type or tick, then save. Add form fields (All tools) can fine-tune them.`);
      },
    });
  }

  async #checkSignatures() {
    const doc = this.doc, tab = this.activeTab;
    this.sigbar.hidden = true;
    if (!doc || !tab) return;
    try {
      // Every signature has a /ByteRange (pdf.js leaves signature fields out
      // of its list of fields): the server looks for one before anything else
      // ("quick"), in the file itself when it's on this computer.
      const body = await this.#about(tab, { quick: "1" });
      const res = body && doc === this.doc && await this.#ask("/api/sign/verify", body, 120000);
      if (!res?.ok || doc !== this.doc || tab !== this.activeTab) return;
      if (res.data.error) throw new Error(res.data.error);
      const list = res.data.signatures || [];
      if (!list.length) return;
      tab.signatures = list;
      const bad = list.filter((s) => s.status === "modified" || s.status === "invalid");
      const names = [...new Set(list.map((s) => s.signer).filter(Boolean))].join(", ");
      this.sigbar.classList.toggle("bad", bad.length > 0);
      this.sigText.textContent = bad.length
        ? `${bad.length === 1 ? "A signature" : `${bad.length} signatures`} no longer hold: the document was changed after signing, or the signature is broken.`
        : `Signed by ${names || "someone"}${list.length > 1 ? ` (${list.length} signatures)` : ""}: not changed since. `
          + (list.every((s) => s.status === "valid") ? "The signer's identity is verified." : "The signer's identity isn't verified (their certificate isn't one this computer trusts).");
      this.sigbar.hidden = false;
    } catch (e) {
      if (e.closed || e.name === "AbortError" || doc !== this.doc) return;  // closed meanwhile, or no answer
      this.sigText.textContent = "This document is signed, but its signatures couldn't be checked: " + (e.message || e);
      this.sigbar.classList.add("bad");
      this.sigbar.hidden = false;
    }
  }

  async #signatureDetails() {
    let list = this.activeTab?.signatures;
    try { list = await this.#verifySignatures(); } catch (e) { this.toast("Couldn't check the signatures: " + (e.message || e)); return; }
    if (!list.length) { this.toast("This document has no signatures."); return; }
    const words = { valid: "Valid: not changed since, and the signer's identity is verified",
      "valid-untrusted": "Valid: not changed since. The signer's identity isn't verified (their certificate isn't one this computer trusts)",
      modified: "Changed after signing", invalid: "Not valid: the signature is broken or doesn't match" };
    const when = (t) => { const d = t && new Date(t); return d && !Number.isNaN(+d) ? d.toLocaleString() : t || ""; };
    const items = list.map((s) => el("li", { class: "rd-sigitem " + (s.status || "") },
      el("b", {}, (s.kind === "timestamp" ? "Document timestamp" : s.signer || "Unknown signer") + (s.email ? ` <${s.email}>` : "")),
      el("div", {}, words[s.status] || s.status || ""),
      s.summary ? el("div", { class: "rd-muted" }, s.summary) : null,
      el("small", {}, [s.time ? "Signed " + when(s.time) + (s.time_source === "timestamp" ? " (time from a timestamp server)" : " (time from the signer's computer)") : "",
        s.reason ? "Reason: " + s.reason : "", s.location ? "Location: " + s.location : "", s.page ? "Page " + s.page : "",
        s.issuer ? "Certificate from " + s.issuer : ""].filter(Boolean).join(" · "))));
    await this._dialog("Signatures", [el("ul", { class: "rd-siglist" }, ...items)], [["ok", "Close", true]]);
  }

  // ---- comparing revisions (overlay.js) -------------------------------------------
  /** Draw another version of this document over it: what changed shows in red
   *  (only in that version) and blue (only in this one). */
  async compareRevision() {
    if (!this.doc) return;
    const others = this.tabs.filter((t) => t !== this.activeTab);
    const answer = await this._dialog("Compare with another revision", [el("p", {},
      "The other version is drawn over this one, page by page: what both have is black, what's only in that version red, "
      + "and what's only in this one blue. Pick the other version:")],
      [...others.map((t) => ["tab:" + t.id, t.name]), ["file", "Choose a file…"], ["cancel", "Cancel"]]);
    let source = null, name = "", password = null;
    if (answer.startsWith("tab:")) {
      const tab = others.find((t) => "tab:" + t.id === answer);
      if (!tab) return;
      let bytes = tab.bytes;
      if (!bytes) {
        try { bytes = await this.#readAll(tab); } catch (e) { this.toast(`Couldn't read “${tab.name}”: ${e.message || e}`, 9000); return; }
      }
      source = { data: bytes.slice() };
      name = tab.name;
      password = tab.password;
    } else if (answer === "file") {
      const file = await this.#pickPdf();
      if (!file) return;
      source = { data: new Uint8Array(await file.arrayBuffer()) };
      name = file.name;
    } else return;
    this.#busy(true);
    try {
      await this.closeCompare();
      const overlay = new Overlay(openDocument, destroyTask, DOC_OPTIONS);
      await overlay.open(source, password, (wrong) => this.#askPassword(wrong));
      this.overlay = overlay;
      overlay.mode = this.overlayMode.value;
      this.overlayOffset.value = "0";
      this.overlayName.textContent = name;
      this.overlaybar.hidden = false;
      await this.#drawOverlays();
      if (overlay.doc.numPages !== this.doc.numPages) {
        this.toast(`That version has ${overlay.doc.numPages} pages, this one ${this.doc.numPages}: if sheets moved, set its page number offset in the bar.`, 7000);
      }
    } catch (e) {
      this.toast("Could not open the other version: " + (e.message || e));
    } finally {
      this.#busy(false);
    }
  }

  async closeCompare() {
    const overlay = this.overlay;
    if (!overlay) return;
    this.overlay = null;
    this.overlaybar.hidden = true;
    for (let i = 0; i < (this.pdfViewer?.pagesCount || 0); i++) overlay.clear(this.pdfViewer.getPageView(i));
    await overlay.close();
  }

  /** Draw the comparison on the pages drawn so far. */
  async #drawOverlays() {
    const overlay = this.overlay;
    if (!overlay || !this.pdfViewer) return;
    for (let i = 0; i < this.pdfViewer.pagesCount; i++) {
      const view = this.pdfViewer.getPageView(i);
      if (view?.canvas?.width) await overlay.draw(view, i + 1).catch(() => {});
      if (this.overlay !== overlay) return;
    }
  }

  // ---- dialogs ----------------------------------------------------------------
  _dialog(title, body, buttons, { ready = null } = {}) {
    return new Promise((resolve) => {
      // Enter in a box of the dialog presses the form's first submit button:
      // that has to be the main one ("Open", not the "Cancel" before it), so
      // where there is a main button the others close the dialog themselves.
      const main = buttons.some(([, , primary]) => primary);
      const form = el("form", { method: "dialog" }, el("h3", {}, title), ...body,
        el("div", { class: "actions" }, ...buttons.map(([value, label, primary]) => (primary || !main
          ? el("button", { value, class: primary ? (primary === "seal" ? "primary seal" : "primary") : null, type: "submit" }, label)
          : el("button", { value, type: "button", onclick: () => dialog.close(value) }, label)))));
      const dialog = el("dialog", { class: "rd-dialog" }, form);
      // ``ready``: whether the main button can do its work yet; if not (Enter
      // in a box before everything needed is given) the dialog stays open.
      if (ready) form.addEventListener("submit", (e) => { if (!ready()) e.preventDefault(); });
      const before = document.activeElement;
      dialog.addEventListener("close", () => {
        resolve(dialog.returnValue);
        dialog.remove();
        // Focus back where it was; opened with nothing focused (a file opening): the page view, or Open a PDF.
        setTimeout(() => {
          if (document.activeElement && document.activeElement !== document.body) return;
          if (document.querySelector("dialog[open]")) return;
          const back = before && before !== document.body && before.isConnected && before.getClientRects().length ? before
            : this.doc && this.container.getClientRects().length ? this.container
            : this.emptyOpenBtn.getClientRects().length ? this.emptyOpenBtn : this.root;
          back.focus({ preventScroll: true });
        }, 0);
      });
      dialog.addEventListener("cancel", () => { dialog.returnValue = "cancel"; });
      this.root.append(dialog);
      dialog.showModal();
    });
  }

  async #askPassword(wrong) {
    const inputId = `rd-pw-${Math.random().toString(36).slice(2)}`;
    const input = el("input", { type: "password", id: inputId, autocomplete: "current-password", spellcheck: "false" });
    const toggle = el("button", { type: "button", class: "pw-toggle", "aria-pressed": "false", "aria-label": "Show password" }, "Show");
    toggle.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      toggle.setAttribute("aria-pressed", String(show));
      toggle.textContent = show ? "Hide" : "Show";
      input.focus();
    });
    const body = [el("p", {}, `“${this.name}” is protected. Enter its password to open it.`),
      // The label names the input only; the Show button sits beside it, outside the label.
      el("div", { class: "rd-field rd-pwfield" }, el("label", { for: inputId }, "Password"), el("span", { class: "rd-pwrow" }, input, toggle))];
    if (wrong) {
      const id = `rd-pw-err-${Math.random().toString(36).slice(2)}`;
      body.push(el("div", { class: "err", role: "alert", id }, "That password is incorrect."));
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", id);
    }
    const pending = this._dialog("Password required", body, [["cancel", "Cancel"], ["ok", "Open", true]]);
    input.focus();
    const answer = await pending;
    return answer === "ok" ? input.value : null;
  }

  async showProperties() {
    const doc = this.doc;
    if (!doc) return;
    let info, metadata, page;
    try {
      ({ info, metadata } = await doc.getMetadata());
      page = await doc.getPage(this.pdfViewer.currentPageNumber);
    } catch (e) {
      if (e.closed) return;  // another tab shown meanwhile
      throw e;
    }
    const [x1, y1, x2, y2] = page.view;
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    const date = (raw) => {
      const d = raw && pdfjs.PDFDateString.toDateObject(raw);
      return d ? d.toLocaleString() : "";
    };
    const size = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
    // -- accessibility -- tagged: says so (MarkInfo) or has a structure tree (pdf.js gives MarkInfo as a Map)
    const mark = await doc.getMarkInfo().catch(() => null);
    const tagged = (mark instanceof Map ? mark.get("Marked") : mark?.Marked) || !!(await page.getStructTree().catch(() => null));
    // -- end of accessibility --
    const rows = [
      ["File", this.activeTab?.path || this.name], ["File size", size(this.bytes?.length ?? this.activeTab?.size ?? 0)],
      ["Title", metadata?.get("dc:title") || info.Title], ["Author", info.Author], ["Subject", info.Subject],
      ["Keywords", info.Keywords], ["Created", date(info.CreationDate)], ["Modified", date(info.ModDate)],
      ["Application", info.Creator], ["PDF producer", info.Producer], ["PDF version", info.PDFFormatVersion],
      ["Pages", String(doc.numPages)],
      ["Tagged (accessible)", tagged ? "Yes" : "No"], ["Language", info.Language || "Not set"],  // -- accessibility --
      ["Page size", `${(w / 72 * 25.4).toFixed(0)} × ${(h / 72 * 25.4).toFixed(0)} mm (${(w / 72).toFixed(2)} × ${(h / 72).toFixed(2)} in)`],
      ["Fast web view", info.IsLinearized ? "Yes" : "No"],
      ["Form", info.IsAcroFormPresent ? "Yes" : info.IsXFAPresent ? "Yes (XFA)" : "No"],
      ["Password protected", this.password ? "Yes" : "No"],
    ].filter(([, v]) => v);
    // -- limits and attached files --
    const perms = await doc.getPermissions().catch(() => null);
    if (perms) rows.push(["Limits", permissionText(perms)]);
    const attached = Object.values((await doc.getAttachments().catch(() => null)) || {});
    const dl = el("dl", { class: "rd-props" });
    for (const [k, v] of rows) dl.append(el("dt", {}, k), el("dd", {}, v));
    const parts = [dl];
    if (attached.length) {
      parts.push(el("h3", {}, `Attached files (${attached.length})`), el("ul", { class: "rd-attached" },
        ...attached.map((a) => el("li", {}, el("span", {}, a.filename, a.description ? el("small", {}, " — " + a.description) : null),
          el("small", {}, size(a.content?.length || 0)),
          el("button", { type: "button", class: "rd-btn", onclick: () => this._saveBytes(a.content, a.filename, () => {}, this.activeTab?.fileId) }, "Save…")))));
    }
    // -- end of limits and attached files --
    await this._dialog("Document properties", parts, [["ok", "Close", true]]);
  }

  // -- accessibility --
  /** Check accessibility: what keeps screen readers out of this document,
   *  and how to fix it (accessibility.py), in a dialog. */
  async checkAccessibility() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return;
    this.toast("Checking accessibility…", 120000);
    let result;
    try {
      const body = new FormData();
      body.append("file", new Blob([await this._fullBytes()], { type: "application/pdf" }), "file.pdf");
      body.append("password", tab.password || "");
      const res = await fetch("/api/accessibility/check", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      result = await res.json();
    } catch (e) {
      this.toast("Couldn't check accessibility: " + (e.message || e));
      return;
    }
    this.toastEl?.remove();
    const pages = (list) => list.length ? ` (page${list.length > 1 ? "s" : ""} ${list.slice(0, 10).join(", ")}${list.length > 10 ? "…" : ""})` : "";
    const items = result.issues.map((i) => el("li", { class: "rd-sigitem invalid" },
      el("b", {}, i.message + pages(i.pages)), el("small", {}, "How to fix: " + i.fix)));
    const body = [el("p", {}, result.passed ? "No problems found." : `${result.issues.length} problem${result.issues.length > 1 ? "s" : ""} found.`)];
    if (items.length) body.push(el("ul", { class: "rd-siglist" }, ...items));
    body.push(el("p", { class: "rd-muted", style: "margin-top: 16px" }, result.note));
    const fix = !result.passed && this.onEdit && this.tools.some(([id]) => id === "tag");
    const answer = await this._dialog("Accessibility", body, [...(fix ? [["tag", "Make accessible…"]] : []), ["ok", "Close", true]]);
    if (answer === "tag") this.#edit("tag");
  }
  // -- end of accessibility --

  /** A toast that explains the tool just chosen: it goes when that tool or
   *  its bar does, or when another bar opens (see #endHint). */
  _hint(message, ms) {
    this.toast(message, ms);
    this.hintEl = this.toastEl;
  }
  _endHint() {
    if (this.hintEl && this.hintEl === this.toastEl) { clearTimeout(this.toastTimer); this.toastEl.remove(); this.toastEl = null; }
    this.hintEl = null;
  }

  toast(message, ms = 4000, action = null) {
    if (!message) return;
    if (this.toastEl) this.toastEl.remove();
    const t = el("div", { class: "rd-toast", role: "status" }, el("span", {}, message));
    // ``action``: [label, run] for one button, or [[label, run, "seal"], [label, run]] for more.
    const actions = !action ? [] : Array.isArray(action[0]) ? action : [action];
    for (const [label, run, kind] of actions) t.append(el("button", { type: "button", class: kind || null, onclick: run }, label));
    if (this.scalebar && !this.scalebar.hidden) {  // over the drawing's top, not its title block
      t.classList.add("top");
      t.style.top = `${this.body.offsetTop + 12}px`;
    }
    this.root.append(t);
    this.toastEl = t;
    clearTimeout(this.toastTimer);
    this.toastHold?.();
    this.toastHold = null;
    // A toast with a button (Undo, Save…) stays 10 s at least, and waits while it's pointed at or focused.
    if (action) this.toastHold = heldTimer(t, Math.max(ms, 10000), () => fadeOut(t));
    else this.toastTimer = setTimeout(() => fadeOut(t), ms);
  }

  /** The view (page, zoom, scroll), to come back to after reloading. */
  #view() {
    return { page: this.pdfViewer.currentPageNumber, scale: this.pdfViewer.currentScaleValue,
      top: this.container.scrollTop, left: this.container.scrollLeft };
  }

  _post(url, bytes, tab, fields = {}, files = []) {
    const body = new FormData();
    body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
    body.append("password", tab.password || "");
    for (const [k, v] of Object.entries(fields)) body.append(k, v);
    for (const f of files) body.append("insert", f, f.name || "insert.pdf");
    return fetch(url, { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
  }

  /** Replace the open document with what ``make(bytes, tab)`` gets back from
   *  the server, keeping the old bytes for Undo. In the queue: a save or a tab
   *  switch asked for meanwhile waits for it. */
  _changeDocument(make, { failure = "Could not change the document: ", goto = null, after = null, signing = false, kind = "page" } = {}) {
    return this._serial(async () => {
      const tab = this.activeTab;
      if (!tab || !this.doc) return;
      // Changes are made by rewriting the file, which a signature can't survive.
      if (!signing && tab.signatures?.length && !tab.signedWarned) {
        if (await this.ask("This document is signed", "Changing it makes its signatures show as broken: they no longer cover what the document says.",
          [["cancel", "Cancel"], ["change", "Change it anyway", true]]) !== "change") return;
        tab.signedWarned = true;
      }
      this.#busy(true);
      try {
        const bytes = await this._currentBytes();
        const res = await make(bytes, tab);
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
        const changed = new Uint8Array(await res.arrayBuffer());
        if (!this.tabs.includes(tab)) return;
        if (kind === "redact") tab.redactionsApplied(bytes, changed);
        else if (kind === "text") tab.textEdited(bytes, changed);
        else tab.pageEdited(bytes, changed);
        if (tab === this.activeTab) { tab.aim(goto ? goto(this.#view()) : this.#view()); await this.#reloadEditing(tab); }
        after?.(res);
      } catch (e) {
        this.toast(failure + (e.message || e));
      } finally {
        this.#busy(false);
      }
    });
  }

  _applyTextEdit(edit) {
    return this._changeDocument((bytes, tab) => this._post("/api/text/edit", bytes, tab, { edits: JSON.stringify([edit]) }), {
      failure: "Could not change the text: ",
      kind: "text",
      after: (res) => {
        // Say when the words couldn't get the document's own font.
        let used = [];
        try { used = JSON.parse(res.headers.get("X-Pdfm-Fonts") || "[]"); } catch (e) {}
        const other = used.find((u) => u.kind !== "same" && !u.asked);
        const notes = this._fontNotes(used);
        if (other) {
          notes.push(other.kind === "installed"
            ? `This PDF's copy of the font lacks some of those letters, so ${other.font} from this computer was used.`
            : `This PDF's copy of the font lacks some of those letters, so a similar font (${other.font}) was used.`);
        }
        if (used.some((u) => u.wider)) notes.push("The line is now longer than its column: to wrap it again, rewrite the whole paragraph (Shift+click it).");
        if (used.some((u) => u.taller)) notes.push("The paragraph now takes more lines than before, so it may run into what's below it. Undo with Ctrl+Z.");
        if (used.some((u) => u.overflow) && !edit.fit) {
          notes.push("The text no longer fits on the page, so the text below it wasn't moved. Shorten it, or let it shrink a little.");
          return this.toast(notes.join(" "), 12000, ["Shrink to fit", () => this._shrinkToFit(edit)]);
        }
        if (used.some((u) => u.overflow)) notes.push("Even a little smaller, the text doesn't fit on the page, so the text below it wasn't moved.");
        if (notes.length) this.toast(notes.join(" "), 8000);
      },
    });
  }

  _undoTextEdit() {
    return this._serial(async () => {
      const tab = this.activeTab;
      if (!tab?.undoDepth) return;
      // Undoing goes back to the bytes before the change; anything added
      // since (drawings, form entries) would go with it.
      if ((this._changed() || tab.bytes !== tab.afterEdit)
          && await this.ask("Undo the change?", "Undoing it also takes away the annotations and form entries made since it.",
            [["cancel", "Cancel"], ["undo", "Undo the change", true]]) !== "undo") return;
      tab.editUndone(this.#view());
      await this.#reloadEditing(tab);
      this.toast("Change undone", 1500);
    });
  }

  /** Redo of the last page or text change Undo took back (a note, too). */
  _redoTextEdit() {
    return this._serial(async () => {
      const tab = this.activeTab;
      if (!tab?.redoDepth) return;
      if (this._changed()
          && await this.ask("Redo the change?", "Redoing it takes away the annotations and form entries made since it was undone.",
            [["cancel", "Cancel"], ["redo", "Redo the change", true]]) !== "redo") return;
      tab.editRedone(this.#view());
      await this.#reloadEditing(tab);
      this.toast("Change redone", 1500);
    });
  }

  /** Which Undo (``redo``: Redo) applies now, the same for the buttons and
   *  the keys: "annot" (pdf.js's history of annotations), "page" (the
   *  reader's own page and text changes, notes and shapes included) or null.
   *  In Edit text and the reader's own markup tools its changes come first,
   *  else pdf.js's. */
  _undoKind(redo = false) {
    const page = !!(redo ? this.activeTab?.redoDepth : this.activeTab?.undoDepth);
    const annot = !!this.uiManager && !!(redo ? this.annotRedo : this.annotUndo);
    if (this.textEditMode || this.markupMode) return page ? "page" : annot ? "annot" : null;
    return annot ? "annot" : page ? "page" : null;
  }

  /** Undo (``redo``: Redo) whichever applies; false if there was nothing. */
  _undo(redo = false) {
    const kind = this._undoKind(redo);
    if (kind === "annot") { if (redo) this.uiManager.redo(); else this.uiManager.undo(); }
    else if (kind === "page") { if (redo) this._redoTextEdit(); else this._undoTextEdit(); }
    return !!kind;
  }

  /** The annotate bar's Undo and Redo: on while there's something for them. */
  _undoButtons() {
    if (!this.undoBtn) return;
    this.undoBtn.disabled = !this._undoKind();
    this.redoBtn.disabled = !this._undoKind(true);
  }

  /** Show the tab's changed bytes, staying in the Edit text tool if it was on. */
  async #reloadEditing(tab) {
    const editing = this.textEditMode ? "EDITTEXT" : this.markupMode, picked = this.pickedImage;
    await this._load(tab);
    this._refresh();
    if (tab !== this.activeTab) return;
    if (editing) {
      this.openAnnotate();
      this._setEditMode(editing);
      if (editing === "IMAGES" && picked) this.pickedImage = picked;  // the picture just moved stays picked
    }
    this._undoButtons();
  }

  // ---- saving, printing, editing ------------------------------------------------
  /** Filled-in form fields or annotations that aren't in the file yet. */
  _changed() { return !!(this.doc && this.doc.annotationStorage.size > 0); }
  #edited() { return this._changed() || !!this.activeTab?.edited; }

  async _currentBytes() {
    this._finishEditing();
    if (!this._changed()) return await this._fullBytes();
    return await this._savedDocument(this.activeTab);
  }

  /** The shown document's bytes; a file read in pieces is read in full now
   *  (and rejects, saying why, when that can't be done). */
  async _fullBytes() {
    if (!this.bytes && this.doc) {
      const tab = this.activeTab;
      if (tab?.url) await this.#readAll(tab);
      else if (tab && !tab.bytes) tab.readInFull(await bounded(this.doc.getData(), WHOLE_MS, "reading the document took too long"));
      if (tab === this.activeTab) this.bytes = tab.bytes;
    }
    return this.bytes;
  }

  #copyName() {
    const stem = this.name.replace(/\.pdf$/i, "");
    return this.#edited() ? `${stem} (edited).pdf` : `${stem}.pdf`;
  }

  /** Save the changes into the file itself when it was opened from this
   *  computer (Ctrl+S); otherwise as a copy. */
  async save({ replace = false } = {}) {
    this.noteCommit?.();  // a note typed but not added yet goes in first
    await this.chain;  // a text change still on its way is included
    this._finishEditing();  // a stroke or note still being made counts (Ctrl+S straight after drawing)
    const tab = this.activeTab;
    if (!this.doc || !tab) return;
    // What Save does follows from the tab's state (see saveAction): a tab with
    // blacked-out parts saves a redacted copy, not over the original (and
    // not again when one is saved and nothing changed since).
    const action = saveAction(tab, { changed: this._changed(), replace });
    if (action === "already redacted") { this.toast("Already saved as a redacted copy", 2000); return; }
    if (action === "redacted copy") { await this.saveRedacted(); return; }
    if (action === "as") { await this.saveCopy(); return; }
    if (action === "nothing") { this.toast("No changes to save", 1500); return; }
    const version = tab.version;
    let bytes;
    try { bytes = await this._currentBytes(); } catch (e) { this.toast("Could not save: " + (e.message || e), 9000, ["Save as…", () => this.saveCopy()]); return; }
    const put = (force) => fetch("/api/save/" + encodeURIComponent(tab.fileId), { method: "POST", body: bytes,
      headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/pdf", "X-Pdfm-Stamp": tab.stamp || "",
        ...(force ? { "X-Pdfm-Force": "1" } : {}) } });
    try {
      let res = await put(false);
      if (res.status === 409) {
        // Cancel does nothing; the copy is a choice of its own.
        const choice = await this.ask(`“${tab.name}” was changed by another program`,
          "It changed since you opened it. Replace it with your version, or keep both by saving yours as a copy.",
          [["cancel", "Cancel"], ["copy", "Save as a copy"], ["replace", "Replace file", true]]);
        if (choice === "copy") { await this.saveCopy(); return; }
        if (choice !== "replace") return;
        res = await put(true);
      }
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error || res.statusText);
      // The file now holds these bytes (and a file read in pieces is in memory now).
      this.#saved(tab, "in place", { version, bytes, stamp: answer.stamp, keep: tab === this.activeTab && !this.bytes });
      // What the file held is kept for a while (not for big files): Undo puts it back.
      if (answer.undo) this.toast(`Saved over ${tab.name}`, 10000, ["Undo", () => this.#unsave(tab, answer.undo)]);
      else this.toast(`Saved over ${tab.name}`, 4000);
    } catch (e) {
      this.toast("Could not save: " + (e.message || e), 9000, ["Save as…", () => this.saveCopy()]);
    }
  }

  /** Undo a save over the file: it gets back what it held; the tab keeps
   *  the changes, unsaved again. */
  async #unsave(tab, undo) {
    this.toastHold?.(); this.toastHold = null;
    this.toastEl?.remove();
    try {
      const res = await fetch("/api/unsave", { method: "POST", body: JSON.stringify({ undo }),
        headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/json" } });
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error || res.statusText);
      if (this.tabs.includes(tab)) tab.saveUndone(answer.stamp);
      this.toast(`Undone: ${tab.name} is as it was before. Your changes are still here, not saved.`, 6000);
    } catch (e) {
      this.toast("Could not undo the save: " + (e.message || e), 9000);
    }
  }

  /** A question in the app's own dialog (never the browser's confirm()).
   *  ``buttons``: [value, label, main?]; resolves with the value of the one
   *  pressed. Escape and Cancel give "cancel", and focus goes back to where it was. */
  ask(title, text, buttons) {
    return this._dialog(title, [text].flat().filter(Boolean).map((t) => el("p", {}, t)), buttons);
  }

  /** Show a file the app has open or saved in its folder (the server checks). */
  async reveal(path) {
    try {
      const res = await fetch("/api/reveal", { method: "POST", body: JSON.stringify({ path }),
        headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/json" } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    } catch (e) { this.toast("Could not show it in its folder: " + (e.message || e), 6000); }
  }

  /** Save an open tab (e.g. a tool's result) as a file, the dialog starting
   *  in the folder of the opened file ``near`` (its id). Resolves with the
   *  path when it's known (the desktop window), true when saved elsewhere, null if not saved. */
  /** Ctrl+S, or Shift for Save as. A tab with blacked-out parts saves a
   *  redacted copy (see saveRedacted), never over its original unasked. */
  _saveKey(e) {
    if (e.shiftKey) this.saveCopy();
    else this.save();
  }

  /** "<name> (redacted).pdf" for a tab with blacked-out parts. */
  #redactedName(tab = this.activeTab) {
    return (tab?.name || "document.pdf").replace(/(?: \(redacted\))?(\.pdf)?$/i, "") + " (redacted).pdf";
  }

  /** Save what's shown, blacked-out parts and all, as a copy beside the
   *  original: "<name> (redacted).pdf", the Save dialog starting in the
   *  original's folder (a download in a plain browser). */
  async saveRedacted() {
    this.noteCommit?.();
    await this.chain;
    this._finishEditing();
    if (!this.doc) return;
    const tab = this.activeTab, version = tab.version;
    let bytes;
    try { bytes = await this._currentBytes(); } catch (e) { this.toast("Could not save the redacted copy: " + (e.message || e), 9000); return; }
    await this._saveBytes(bytes, this.#redactedName(tab), () => this.#saved(tab, "redacted", { version }), tab?.fileId);
  }

  /** Write the redacted version over the original file, once asked. */
  async replaceWithRedacted() {
    const tab = this.activeTab;
    if (!tab?.fileId) { await this.saveRedacted(); return; }
    const answer = await this.ask(`Replace ${tab.name} with the redacted version?`, "The original text can’t be recovered from it.",
      [["cancel", "Cancel"], ["replace", "Replace original", true]]);
    if (answer !== "replace" || this.activeTab !== tab) return;
    await this.save({ replace: true });
  }

  async saveTab(tab, near = null) {
    await this.chain;
    if (!tab || !this.tabs.includes(tab)) return null;
    const version = tab.version;
    let bytes;
    try { bytes = tab === this.activeTab ? await this._currentBytes() : tab.bytes || tab.original; }
    catch (e) { this.toast("Could not save: " + (e.message || e), 9000); return null; }
    if (!bytes) return null;
    let done = null;
    const name = /\.pdf$/i.test(tab.name) ? tab.name : tab.name + ".pdf";
    await this._saveBytes(bytes, name, (where) => { done = where || true; this.#saved(tab, "as", { version, made: true }); }, near);
    return done;
  }

  /** Save as a new file, starting in the document's own folder (Ctrl+Shift+S). */
  async saveCopy() {
    await this.chain;  // a text change still on its way is included
    this._finishEditing();
    if (!this.doc) return;
    const tab = this.activeTab, version = tab.version;
    let bytes;
    try { bytes = await this._currentBytes(); } catch (e) { this.toast("Could not save a copy: " + (e.message || e), 9000); return; }
    await this._saveBytes(bytes, this.#copyName(), () => this.#saved(tab, "as", { version }), tab?.fileId);
  }

  /** Save ``bytes`` as a file the person picks (or download it); the
   *  dialog starts next to the opened file ``near`` (its id). */
  async _saveBytes(bytes, name, saved = () => {}, near = null) {
    const api = this.api();
    if (this.isDesktop() && api && api.save_result) {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), name);
      try {
        const res = await fetch("/api/keep", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
        const { id } = await res.json();
        const path = await api.save_result(id, name, near);
        if (path) { saved(path); this.toast("Saved to " + path, 5000, ["Show in folder", () => this.reveal(path)]); }
      } catch (e) { this.toast("Could not save: " + (e.message || e)); }
      return;
    }
    const blob = new Blob([bytes], { type: "application/pdf" });
    if (files.canSave) {
      try {
        const where = await files.saveAs(blob, name);
        if (where) { saved(); this.toast("Saved as " + where); }
        return;
      } catch (e) {
        if (e.name !== "SecurityError") { this.toast("Could not save: " + (e.message || e)); return; }
        // not called from a click (e.g. Ctrl+S in some browsers): download instead
      }
    }
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    saved();
  }

  // ---- the page menu (right-click) ---------------------------------------------------------
  #pageMenu(e) {
    if (!this.doc || e.target.closest?.(".rd-textedit-box, input, textarea")) return;
    const thumb = e.target.closest?.(".rd-thumb");
    const pageEl = e.target.closest?.(".page");
    const page = Number(thumb?.dataset.page || pageEl?.dataset.pageNumber);
    if (!page) return;
    e.preventDefault();
    // On a thumbnail, the menu acts on the selected pages if it's one of them.
    const pages = thumb && this.pickedPages.has(page) ? [...this.pickedPages].sort((a, b) => a - b) : [page];
    if (thumb && !this.pickedPages.has(page)) this.#select([page]);
    const what = Reader.#pagesText(pages).replace(/^Page \d+$/, "this page");
    const item = (icon, label, run, keys = "") => {
      const b = el("button", { type: "button", role: "menuitem" });
      b.innerHTML = svg(icon);
      b.append(el("span", {}, label));
      if (keys) b.append(el("kbd", {}, keys));
      b.addEventListener("click", () => { this.#closeContextMenu(); run(); });
      return b;
    };
    const items = [];
    const selection = String(window.getSelection() || "").trim();
    if (!thumb) {
      if (selection) items.push(item("copy", "Copy", () => navigator.clipboard?.writeText(selection).then(() => this.toast("Copied", 1200), () => {}), "Ctrl+C"));
      const span = e.target.closest?.(".textLayer span");
      if (span && !span.classList.contains("markedContent") && span.textContent.trim()) {
        items.push(item("edittext", "Edit this text", () => { this.useTool("EDITTEXT"); this._openTextEditor(span, pageEl, page, "line"); }));
        items.push(item("edittext", "Rewrite this paragraph", () => { this.useTool("EDITTEXT"); this._openTextEditor(span, pageEl, page, "paragraph"); }));
      }
      const at = { clientX: e.clientX, clientY: e.clientY };
      items.push(item("text", "Add text here", () => { this.useTool("EDITTEXT"); this._openNewText(at, pageEl, page); }));
      items.push(el("hr"));
    }
    items.push(
      item("cw", `Turn ${what} clockwise`, () => this.rotatePages(pages, 90)),
      item("ccw", `Turn ${what} counter-clockwise`, () => this.rotatePages(pages, -90)),
      item("copypage", `Duplicate ${what}`, () => this.duplicatePages(pages)),
      item("trash", `Delete ${what}`, () => this.deletePages(pages), thumb ? "Del" : ""),
      el("hr"),
      item("save", `Extract ${what} as a new PDF…`, () => this.extractPages(pages)),
      item("open", "Insert a PDF after it…", () => this.insertPdf(Math.max(...pages))),
    );
    if (this.onCommands) items.push(el("hr"), item("command", "All commands…", () => this.onCommands(), "Ctrl+K"));
    this.ctxMenu.replaceChildren(...items);
    this.ctxMenu.hidden = false;
    // Where the click was, kept inside the reader.
    const box = this.root.getBoundingClientRect(), menu = this.ctxMenu.getBoundingClientRect();
    const x = Math.min(e.clientX - box.left, box.width - menu.width - 8);
    const y = Math.min(e.clientY - box.top, box.height - menu.height - 8);
    Object.assign(this.ctxMenu.style, { left: `${Math.max(8, x)}px`, top: `${Math.max(8, y)}px`, right: "auto" });
    this.ctxMenu.querySelector("button")?.focus({ preventScroll: true });
  }
  #closeContextMenu() { if (!this.ctxMenu.hidden) this.ctxMenu.hidden = true; }

  // ---- pages: turn, delete, copy, move, insert, extract -------------------------------------
  #pagePlan(count = this.doc.numPages) { return Array.from({ length: count }, (_, i) => ({ page: i + 1 })); }
  static #pagesText(pages) { return pages.length === 1 ? `Page ${pages[0]}` : `${pages.length} pages`; }

  /** ``plan``: the pages' new order, or a function making it for the
   *  document shown when the change's turn comes (a change asked for while
   *  another tab is still being shown waits for it, and is that tab's). A
   *  plan made before then is for the document shown when it was asked:
   *  if another one is shown by its turn, nothing is changed. */
  #arrange(plan, { inserts = [], message = "", page = null } = {}) {
    const asked = this.doc;
    return this._changeDocument((bytes, tab) => {
      if (typeof plan !== "function" && this.doc !== asked) throw new Error("the document changed meanwhile, so nothing was changed");
      const pages = typeof plan === "function" ? plan(this.doc.numPages) : plan;
      return this._post("/api/pages/arrange", bytes, tab, { plan: JSON.stringify(pages) }, inserts);
    }, {
      failure: "Could not change the pages: ",
      goto: (view) => ({ page: page || view.page, scale: view.scale }),
      after: () => { if (message) this.toast(message, 5000, ["Undo", () => this._undoTextEdit()]); },
    });
  }
  rotatePages(pages, delta) {
    if (!(this.doc || this.tabs.length) || !pages.length) return;  // (just after a tab switch: that tab's pages, once it's shown)
    const plan = (count) => {
      if (!pages.some((n) => n <= count)) throw new Error("that page isn't in this document");
      return this.#pagePlan(count).map((e) => (pages.includes(e.page) ? { ...e, rotate: delta } : e));
    };
    return this.#arrange(plan, { page: pages[0], message: `${Reader.#pagesText(pages)} turned` });
  }
  deletePages(pages) {
    if (!this.doc || !pages.length) return;
    if (pages.length >= this.doc.numPages) { this.toast("A PDF needs at least one page: keep one, or close the document."); return; }
    const plan = this.#pagePlan().filter((e) => !pages.includes(e.page));
    return this.#arrange(plan, { page: Math.min(Math.min(...pages), plan.length), message: `${Reader.#pagesText(pages)} deleted` });
  }
  duplicatePages(pages) {
    if (!this.doc || !pages.length) return;
    const last = Math.max(...pages), plan = this.#pagePlan();
    plan.splice(last, 0, ...[...pages].sort((a, b) => a - b).map((page) => ({ page })));
    return this.#arrange(plan, { page: last + 1, message: `${Reader.#pagesText(pages)} copied` });
  }
  /** Move ``pages`` so they come before page ``before`` (pages + 1 = the end). */
  movePages(pages, before) {
    if (!this.doc || !pages.length) return;
    const moving = [...pages].sort((a, b) => a - b).map((page) => ({ page }));
    const rest = this.#pagePlan().filter((e) => !pages.includes(e.page));
    let at = rest.findIndex((e) => e.page >= before);
    if (at < 0) at = rest.length;
    rest.splice(at, 0, ...moving);
    if (rest.every((e, i) => e.page === i + 1)) return;  // nothing moved
    return this.#arrange(rest, { page: at + 1, message: `${Reader.#pagesText(pages)} moved` });
  }
  /** Put a PDF's pages after page ``after`` (0 = at the start). */
  async insertPdf(after, file = null) {
    if (!this.doc) return;
    file = file || await this.#pickPdf();
    if (!file) return;
    const plan = this.#pagePlan();
    plan.splice(after, 0, { file: 1 });
    return this.#arrange(plan, { inserts: [file], page: after + 1, message: `${file.name} inserted` });
  }
  #pickPdf() {
    return new Promise((resolve) => {
      const input = el("input", { type: "file", accept: ".pdf,application/pdf", hidden: true });
      input.addEventListener("change", () => { resolve(input.files[0] || null); input.remove(); });
      input.addEventListener("cancel", () => { resolve(null); input.remove(); });
      this.root.append(input);
      input.click();
    });
  }
  /** Save the pages as a PDF of their own; the document stays as it is. */
  async extractPages(pages) {
    await this.chain;
    if (!this.doc || !pages.length) return;
    const tab = this.activeTab;
    this.#busy(true);
    try {
      const bytes = await this._currentBytes();
      const plan = [...pages].sort((a, b) => a - b).map((page) => ({ page }));
      const res = await this._post("/api/pages/arrange", bytes, tab, { plan: JSON.stringify(plan) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const out = new Uint8Array(await res.arrayBuffer());
      const stem = this.name.replace(/\.pdf$/i, "");
      const label = pages.length === 1 ? `page ${pages[0]}` : `${pages.length} pages`;
      this.#busy(false);
      await this._saveBytes(out, `${stem} (${label}).pdf`);
    } catch (e) {
      this.toast("Could not extract the pages: " + (e.message || e));
    } finally {
      this.#busy(false);
    }
  }

  /** Commit the annotation being drawn or typed. */
  _finishEditing() {
    try { this.uiManager?.endCurrentEditing?.(); } catch (e) { /* nothing being edited */ }
  }

  /** ``tab``'s changes up to ``version`` are in a file now (see TabState.saved;
   *  ``how``: "in place", "as" or "redacted"). pdf.js's undo history outlives
   *  the save: Save goes quiet until the next change, and Undo back to here
   *  is unchanged again. */
  #saved(tab, how, details) {
    if (!tab || !this.tabs.includes(tab)) return;
    const here = tab === this.activeTab && this.doc;
    const current = tab.saved(how, { ...details, doc: here ? this.doc : null, print: here ? this.#annotPrint() : "" });
    try { if (current && here) this.doc.annotationStorage.resetModified(); } catch (e) { /* (so the next change is noticed) */ }
  }

  /** What the annotations and form entries not in the file yet are, as
   *  text: the same text, the same changes. (Each editor as it would be
   *  saved, without its picture's pixels.) */
  #annotPrint(doc = this.doc) {
    const parts = [];
    for (const [key, value] of doc?.annotationStorage || []) {
      let said = value;
      if (typeof value?.serialize === "function") { try { said = value.serialize(false); } catch (e) { said = "?"; } }
      if (said) parts.push(key + ":" + JSON.stringify(said));
    }
    return parts.join("\n");
  }

  /** pdf.js's Undo and Redo don't say when the document is back as it was
   *  opened or last saved. It is when the shown document is the one it was
   *  then (no page or text change since: those load the document again)
   *  and its annotations and entries are the same: then it isn't changed
   *  (no dot, Save quiet). Anything else than that state is a change
   *  (see TabState.annotationsAre). */
  #compareAnnotations(tab = this.activeTab) {
    if (!tab || tab !== this.activeTab || !this.doc || tab.clean?.doc !== this.doc) return;
    if (tab.annotationsAre(this.doc, this.#annotPrint()) === "saved") {
      try { this.doc.annotationStorage.resetModified(); } catch (e) { /* (so the next change is noticed) */ }
    }
  }

  print() {
    // (Another tab shown while the pages are prepared: nothing is printed.)
    return this.#print().catch((e) => { if (!e?.closed) throw e; });
  }

  async #print() {
    if (!this.doc) return;
    const doc = this.doc, total = doc.numPages;
    if (total > 300 && await this.ask(`Print all ${total} pages?`, "Preparing them can take a while.",
      [["cancel", "Cancel"], ["print", "Prepare to print", true]]) !== "print") return;
    let box = document.getElementById("rd-print");
    if (box) box.remove();
    box = el("div", { id: "rd-print" });
    document.body.append(box);
    // The print dialog starts in the document's orientation (a landscape
    // sheet on portrait paper prints small).
    const first = (await doc.getPage(1)).getViewport({ scale: 1 });
    const orientation = el("style", { id: "rd-print-page" }, `@media print { @page { size: ${first.width > first.height ? "landscape" : "portrait"}; margin: 0; } }`);
    document.getElementById("rd-print-page")?.remove();
    document.head.append(orientation);
    const layers = this._printLayers();  // the layers shown (undefined: the document has none)
    try {
      for (let n = 1; n <= total; n++) {
        this.toast(`Preparing page ${n} of ${total} for printing…`, 60000);
        const page = await doc.getPage(n);
        const viewport = page.getViewport({ scale: 150 / 72 });  // 150 dpi
        const canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
        await page.render({ canvas, viewport, intent: "print", annotationMode: pdfjs.AnnotationMode.ENABLE_STORAGE,
          printAnnotationStorage: doc.annotationStorage.print, optionalContentConfigPromise: layers }).promise;

        const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
        box.append(el("div", { class: "rd-print-page" }, el("img", { src: URL.createObjectURL(blob), alt: "" })));
        if (doc !== this.doc) return;
      }
      if (this.toastEl) this.toastEl.remove();
      await Promise.all([...box.querySelectorAll("img")].map((img) => img.decode().catch(() => {})));
      document.body.classList.add("rd-printing");
      window.print();
    } finally {
      setTimeout(() => {
        document.body.classList.remove("rd-printing");
        for (const img of box.querySelectorAll("img")) URL.revokeObjectURL(img.src);
        box.remove();
        orientation.remove();
      }, 1000);
    }
  }

  // ---- for the command palette and the page menu ----------------------------------------
  /** Switch on an annotation tool or Edit text (EDITTEXT); NONE goes back to reading. */
  useTool(name) {
    if (!this.doc) return;
    if (name === "NONE") { this.closeAnnotate(); return; }
    this.openAnnotate();
    this._setEditMode(name, name === "STAMP");
  }
  editWith(toolId) { return this.#edit(toolId); }
  goToPage(n) { if (this.doc && n >= 1 && n <= this.pdfViewer.pagesCount) this.pdfViewer.currentPageNumber = n; }
  showBookmarks() { this.#setSidebar(true); this.#showTab("outline"); }


  /** What there is to do with the open document, for the command palette:
   *  [{label, keys?, words?, run}]. */
  commands() {
    const open = { icon: "open", label: "Open a PDF", keys: "Ctrl+O", words: "file browse", run: () => this.chooseFile() };
    if (!this.doc) return [open];
    const v = this.pdfViewer;
    // (Their single keys work while the annotate bar is open.)
    const tool = (label, mode, words = "", icon = "pen", keys = "") => ({ icon, label, words: "annotate " + words, run: () => this.useTool(mode), ...(keys ? { keys } : {}) });
    const list = [
      { icon: "edittext", label: "Edit text", keys: "E", words: "change words typo rewrite paragraph add text", run: () => this.useTool("EDITTEXT") },
      { icon: "search", label: "Find in document", keys: "Ctrl+F", words: "search look for", run: () => this.openFind() },
      tool("Highlight text", "HIGHLIGHT", "mark marker", "highlight", "H"), tool("Add text", "FREETEXT", "text box note comment type", "text", "A"),
      tool("Draw", "INK", "pen ink freehand", "draw", "D"), tool("Add a picture", "STAMP", "image photo stamp", "image"),
      tool("Add a note", "NOTE", "comment sticky review", "note", "C"), tool("Underline text", "UNDERLINE", "markup", "underline"),
      tool("Strike out text", "STRIKEOUT", "strikethrough delete markup", "strike"),
      tool("Draw a shape", "SHAPE", "rectangle ellipse circle cloud arrow line markup revision", "shapes"),
      tool("Stamp the page", "STAMPS", "approved draft confidential final received reviewed rubber stamp", "stamp"),
      tool("Edit pictures", "IMAGES", "image photo move resize replace delete logo", "imgedit"),
      tool("Edit links", "LINKS", "hyperlink url web address page add change remove", "link"),
      tool("Digital ID", "CERTSIGN", "sign with a digital id certificate pfx p12 digital signature sign", "badge"),
      tool("Redact this document", "REDACT", "redaction black out for good remove hide censor private confidential", "redact"),
      { icon: "badge", label: "Check the signatures", words: "verify valid certificate signed", run: () => this.#signatureDetails() },
      { icon: "comments", label: "Comments", words: "notes replies review list markup", run: () => { this.#setSidebar(true); this.#showTab("comments"); } },
      { icon: "sign", label: "Sign", sub: "draw or type your signature", words: "signature autograph pen draw type initials", run: () => this.sign() },
      { icon: "cw", label: "Rotate the view clockwise", keys: "R", words: "turn right", run: () => this.rotate(90) },
      { icon: "ccw", label: "Rotate the view counter-clockwise", keys: "Shift+R", words: "turn left anticlockwise", run: () => this.rotate(-90) },
      { icon: "cw", label: "Turn this page clockwise (in the file)", words: "rotate page save", run: () => this.rotatePages([v.currentPageNumber], 90) },
      { icon: "ccw", label: "Turn this page counter-clockwise (in the file)", words: "rotate page save", run: () => this.rotatePages([v.currentPageNumber], -90) },
      { icon: "trash", label: "Delete this page", words: "remove page", run: () => this.deletePages([v.currentPageNumber]) },
      { icon: "copypage", label: "Duplicate this page", words: "copy page", run: () => this.duplicatePages([v.currentPageNumber]) },
      { icon: "save", label: "Extract this page as a new PDF", words: "split save page", run: () => this.extractPages([v.currentPageNumber]) },
      { icon: "open", label: "Insert a PDF after this page", words: "add merge combine pages", run: () => this.insertPdf(v.currentPageNumber) },
      { icon: "sidebar", label: this.sidebar.hidden ? "Show thumbnails" : "Hide thumbnails", words: "sidebar pages", run: () => this.toggleSidebar() },
      { icon: "book", label: "Show bookmarks", words: "outline contents chapters sidebar", run: () => this.showBookmarks() },
      ...(this.layers ? [{ icon: "layers", label: "Show layers", words: "optional content cad drawing hide sidebar",
        run: () => { this.layersWanted = true; this.#setSidebar(true); this.#showTab("layers"); } }] : []),
      { icon: "full", label: "Fit page", words: "zoom whole", run: () => { v.currentScaleValue = "page-fit"; } },
      { icon: "spread", label: "Fit width", words: "zoom", run: () => { v.currentScaleValue = "page-width"; } },
      { icon: "plus", label: "Actual size", words: "zoom 100%", run: () => { v.currentScaleValue = "page-actual"; } },
      { icon: "ruler", label: "True paper size", words: "zoom real scale blueprint plan drawing", run: () => this._viewAt("true") },
      { icon: "ruler", label: "Measure a distance", keys: "M", words: "length dimension blueprint plan scale ruler snap", run: () => this.measureTool("distance") },
      { icon: "area", label: "Measure an area", words: "room square feet metres blueprint plan scale", run: () => this.measureTool("area") },
      { icon: "rect", label: "Measure a rectangle", words: "room area two corners blueprint takeoff", run: () => this.measureTool("rect") },
      { icon: "cutout", label: "Cut an opening out of an area", words: "subtract hole stair shaft column deduct takeoff", run: () => this.measureTool("cutout") },
      { icon: "count", label: "Count things", words: "outlets doors fixtures windows tally takeoff", run: () => this.measureTool("count") },
      { icon: "angle", label: "Measure an angle", words: "degrees corner slope", run: () => this.measureTool("angle") },
      { icon: "layers", label: this.overlay ? "Stop comparing revisions" : "Compare with another revision", words: "overlay difference drawing changes old new version",
        run: () => (this.overlay ? this.closeCompare() : this.compareRevision()) },
      { icon: "csv", label: "Save the measurements as CSV", words: "excel spreadsheet export takeoff", run: () => this._exportMeasures() },
      { icon: "pdfin", label: "Put the measurements in the PDF", words: "annotations save keep markup", run: () => this._measuresIntoPdf() },
      { icon: "path", label: "Measure a run of lines (path)", words: "polyline walls total length baseboard blueprint", run: () => this.measureTool("path") },
      { icon: "list", label: "List of measurements", words: "totals copy units blueprint takeoff", run: () => { this.toggleScaleBar(true); this._toggleMeasPanel(true); } },
      { icon: "straight", label: this.straightOnly ? "Measure at any angle" : "Measure straight lines only", words: "horizontal vertical orthogonal blueprint", run: () => this.straightBtn.click() },
      { icon: "ruler", label: "Set the drawing scale from a dimension", words: "blueprint plan calibrate known length", run: () => this.measureTool("calibrate") },
      { icon: "ruler", label: "Drawing scale", words: "blueprint plan sheet show at measure", run: () => this.toggleScaleBar(true) },
      { icon: "screen", label: "Calibrate the screen for true size", words: "monitor dpi ppi real size card", run: () => this.calibrateScreen() },
      { icon: "plus", label: "Zoom to a percentage…", words: "custom zoom type number", run: () => { this.zoomInput.focus(); } },
      { icon: "plus", label: "Zoom in", keys: "Ctrl +", run: () => v.increaseScale() },
      { icon: "minus", label: "Zoom out", keys: "Ctrl −", run: () => v.decreaseScale() },
      { icon: "up", label: "First page", keys: "Home", words: "start beginning", run: () => this.goToPage(1) },
      { icon: "down", label: "Last page", keys: "End", words: "end", run: () => this.goToPage(v.pagesCount) },
      { icon: "spread", label: "Two-page view", words: "spread book", run: () => this.toggleSpread() },
      { icon: "moon", label: "Dark pages", words: "night invert", run: () => this.toggleDarkPages() },
      { icon: "hand", label: "Hand tool", words: "drag scroll pan", run: () => this.toggleHand() },
      { icon: "full", label: "Full screen", words: "presentation", run: () => this.toggleFullscreen() },
      { icon: "print", label: "Print", keys: "Ctrl+P", run: () => this.print() },
      ...(this.activeTab?.fileId ? [{ icon: "save", label: "Save", keys: "Ctrl+S", words: "write file", run: () => this.save() }] : []),
      { icon: "save", label: this.activeTab?.fileId ? "Save as" : this.#edited() ? "Save a copy with your changes" : "Save a copy",
        keys: this.activeTab?.fileId ? "Ctrl+Shift+S" : "Ctrl+S", words: "download export copy", run: () => this.saveCopy() },
      { icon: "info", label: "Document properties", words: "info metadata author title", run: () => this.showProperties() },
      { icon: "x", label: "Close this document", words: "tab", run: () => this.closeTab() },
      open,
    ];
    return list.map((c) => ({ ...c, svg: svg(c.icon || "book") }));
  }

  async #edit(toolId) {
    await this.chain;
    if (!this.doc || !this.onEdit) return;
    const bytes = await this._currentBytes();
    const file = new File([bytes], this.#copyName(), { type: "application/pdf" });
    if (this.isPristine()) file.openedId = this.activeTab.fileId;  // the same file handed over twice is one file
    this.onEdit(toolId, file, this.password);
  }
}
