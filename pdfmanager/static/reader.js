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

import { BASE, DOC_OPTIONS, destroyTask, loadPdfjs as loadLibrary, openDocument } from "./pdfjs.js";
import * as files from "./files.js";
import * as measure from "./measure.js";
import { Overlay } from "./overlay.js";
let snap = null;  // snapping to a drawing's lines (snap.js); the reader works without it
const snapReady = import("./snap.js").then((m) => { snap = m; }, () => {});

let pdfjs = null;   // pdf.js display API
let viewer = null;  // pdf.js viewer components

/** pdf.js and its viewer components. Called early (see gui.html) so the
 *  reader is ready by the time it's opened. */
export async function loadPdfjs() {
  if (viewer) return;
  pdfjs = await loadLibrary();
  globalThis.pdfjsLib = pdfjs;  // the viewer components read the API from here
  viewer ||= await import(BASE + "web/pdf_viewer.mjs");
  if (!document.querySelector("link[data-pdfjs]")) {
    const link = document.createElement("link");
    link.rel = "stylesheet"; link.href = BASE + "web/pdf_viewer.css"; link.dataset.pdfjs = "";
    document.head.append(link);
    await new Promise((resolve) => { link.onload = resolve; link.onerror = resolve; });
  }
}

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

const ICON = {
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  open: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  prev: '<path d="m15 18-6-6 6-6"/>', next: '<path d="m9 18 6-6-6-6"/>',
  up: '<path d="m18 15-6-6-6 6"/>', down: '<path d="m6 9 6 6 6-6"/>',
  minus: '<path d="M5 12h14"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  ccw: '<path d="M4 11a8 8 0 1 1 2.3 5.7"/><path d="M4 4v7h7"/>', cw: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>', check: '<path d="M20 6 9 17l-5-5"/>',
  book: '<path d="M2 5h6a4 4 0 0 1 4 4v11a3 3 0 0 0-3-3H2z"/><path d="M22 5h-6a4 4 0 0 0-4 4v11a3 3 0 0 1 3-3h7z"/>',
  spread: '<rect x="2" y="5" width="9" height="14" rx="1"/><rect x="13" y="5" width="9" height="14" rx="1"/>',
  hand: '<path d="M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.9-6-2.4l-3.6-3.6a2 2 0 0 1 2.8-2.8L7 15"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  full: '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  print: '<path d="M6 9V2h12v7"/><rect x="6" y="14" width="12" height="8"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>',
  save: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>', home: '<path d="M4 11 12 4l8 7"/><path d="M6 9.5V20h12V9.5"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  tool: '<path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-3-3 8-8-1.3-1.3a4 4 0 0 0-5-5z"/><path d="m4 20 4-4"/>',
  twisty: '<path d="m9 6 6 6-6 6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  path: '<path d="M3 18 9 9l5 6 7-10"/><circle cx="3" cy="18" r="1.3"/><circle cx="9" cy="9" r="1.3"/><circle cx="14" cy="15" r="1.3"/><circle cx="21" cy="5" r="1.3"/>',
  straight: '<path d="M5 4v15h15"/><path d="M9 15h7V8" stroke-dasharray="2.5 2.5"/>',
  copypage: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h2"/><path d="M14 12v5M11.5 14.5h5"/>',
  command: '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  pointer: '<path d="m4 4 7 17 2.5-7.5L21 11z"/>',
  highlight: '<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>',
  text: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
  draw: '<path d="M3 17c3-3 5-9 8-9s1 8 4 8 4-4 6-6"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  sign: '<path d="M3 17c2-1 3-5 5-5s0 5 2 5 3-6 5-6 1 6 3 6 2-2 3-2"/><path d="M3 21h18"/>',
  edittext: '<path d="M4 7V5h11v2M9.5 5v13M7 18h4"/><path d="m14 20 6.5-6.5-2-2L12 18v2z"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  ruler: '<path d="M3 16.5 16.5 3 21 7.5 7.5 21z"/><path d="m7.5 12 2 2M10.5 9l2 2M13.5 6l2 2"/>',
  area: '<path d="M4 7 11 3l9 5-2.5 11L6 20z"/><circle cx="4" cy="7" r="1.2"/><circle cx="11" cy="3" r="1.2"/><circle cx="20" cy="8" r="1.2"/>',
  screen: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  note: '<path d="M4 5h16v11H10l-4 4v-4H4z"/><path d="M8 9h8M8 12h5"/>',
  imgedit: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="m3 13 4-4 6 6"/><path d="M14 20h7M17.5 16.5v7" transform="translate(-1 -3)"/>',
  fillable: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h4M7 14h4"/><rect x="13" y="7.5" width="5" height="3" rx=".5"/><rect x="13" y="12.5" width="3" height="3" rx=".5"/>',
  badge: '<path d="M12 2l2.4 1.8 3 .2.9 2.9 2.3 1.9-.9 2.9.9 2.9-2.3 1.9-.9 2.9-3 .2L12 22l-2.4-1.8-3-.2-.9-2.9-2.3-1.9.9-2.9-.9-2.9 2.3-1.9.9-2.9 3-.2z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
  redact: '<path d="M4 6h16M4 18h9"/><rect x="4" y="10" width="13" height="4" rx="1" fill="currentColor"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  rect: '<rect x="4" y="6" width="16" height="12" rx="1"/><circle cx="4" cy="6" r="1.4"/><circle cx="20" cy="18" r="1.4"/>',
  cutout: '<rect x="3" y="4" width="18" height="16" rx="1"/><rect x="9" y="9" width="7" height="6" stroke-dasharray="2 2"/>',
  count: '<circle cx="7" cy="8" r="3"/><circle cx="17" cy="8" r="3"/><circle cx="12" cy="17" r="3"/>',
  angle: '<path d="M4 20h16M4 20 15 6"/><path d="M10.5 20a6.5 6.5 0 0 0-2.3-5"/>',
  csv: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h8"/>',
  pdfin: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M12 11v6M9 14l3 3 3-3"/>',
  underline: '<path d="M7 4v6a5 5 0 0 0 10 0V4"/><path d="M5 20h14"/>',
  strike: '<path d="M16 6.5C15 5 13.6 4.5 12 4.5c-2.2 0-4 1.2-4 3.2 0 1.5 1 2.4 2.6 3"/><path d="M4 12h16"/><path d="M8 17.5c1 1.4 2.4 2 4 2 2.3 0 4-1.3 4-3.3 0-.8-.3-1.5-.8-2"/>',
  shapes: '<rect x="3" y="11" width="9" height="9" rx="1"/><circle cx="16" cy="8" r="5"/>',
  stamp: '<path d="M9 3h6l-1 7h4a2 2 0 0 1 2 2v3H4v-3a2 2 0 0 1 2-2h4z"/><path d="M5 19h14"/>',
  comments: '<path d="M4 5h13v9H9l-3 3v-3H4z"/><path d="M17 9h3v8h-2v3l-3-3h-4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  access: '<circle cx="12" cy="4.5" r="1.8"/><path d="M5 8.5l7 1.5 7-1.5M12 10v4.5M12 14.5 8.5 21M12 14.5l3.5 6.5"/>',  // -- accessibility --
};

// Annotation colours (highlights use the pale versions, as on paper).
const COLORS = [["Black", "#000000", "#bdbdbd"], ["Blue", "#1f4fd8", "#80ebff"], ["Red", "#d52b1e", "#ff4f5f"],
  ["Green", "#1f8a3b", "#53ffbc"], ["Yellow", "#b8860b", "#ffff98"]];
// Notes, underline, strikeout and shapes: the reader's own tools, written into
// the file by the server (markup.py) as standard annotations.
const MARKUP = new Set(["NOTE", "UNDERLINE", "STRIKEOUT", "SHAPE", "IMAGES", "LINKS", "CERTSIGN", "STAMPS", "REDACT"]);
// Rubber stamps: their words (markup.py draws them in their colours).
const STAMPS = ["APPROVED", "NOT APPROVED", "REVIEWED", "RECEIVED", "DRAFT", "FINAL", "CONFIDENTIAL",
  "FOR COMMENT", "VOID"];
const SHAPES = [["rect", "Rectangle"], ["ellipse", "Ellipse"], ["cloud", "Cloud"], ["arrow", "Arrow"], ["line", "Line"]];
const KIND_NAMES = { Text: "Note", FreeText: "Text", Highlight: "Highlight", Underline: "Underline", StrikeOut: "Strikeout",
  Squiggly: "Squiggly", Square: "Rectangle", Circle: "Ellipse", Line: "Line", Polygon: "Shape", PolyLine: "Lines",
  Ink: "Drawing", Stamp: "Stamp", Caret: "Insert", FileAttachment: "Attachment", Sound: "Sound" };
const HIGHLIGHT_COLORS = "yellow=#FFFF98,green=#53FFBC,blue=#80EBFF,pink=#FFCBE6,red=#FF4F5F";
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

const MIN_ZOOM = 0.1, MAX_ZOOM = 10;  // what the viewer can draw
let measureSeq = 0;  // numbers the measurements
// Measurement groups' colours, in the order the groups are made.
const GROUP_COLORS = ["#2b6cb0", "#c0392b", "#1f8a3b", "#c77700", "#7b3fb5", "#0f8a8a", "#b83280", "#5a6b2c"];
const MEASURE_KINDS = { distance: "Length", path: "Path", area: "Area", count: "Count", angle: "Angle" };
const SNAP_PX = 12;  // how close (on screen) a measuring click snaps to a line of the drawing
const GEOMETRY_PAGES = 8;  // how many pages' lines to keep per document
const isScale = (s) => !!s && typeof s === "object" && Number.isFinite(s.ratio) && s.ratio > 0 && (s.units === "ft" || s.units === "m") && typeof s.label === "string";

const ZOOM_PRESETS = [
  ["auto", "Automatic"], ["page-fit", "Fit page"], ["page-width", "Fit width"], ["page-actual", "Actual size"], ["true-size", "True paper size"],
  ["0.5", "50%"], ["0.75", "75%"], ["1", "100%"], ["1.25", "125%"], ["1.5", "150%"], ["2", "200%"], ["3", "300%"], ["4", "400%"],
];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}
let tabSeq = 0;
// Files bigger than this, opened from this computer, are read in pieces as
// pages are shown (the whole file only when a change or save needs it).
const LAZY_BYTES = 32 * 1024 * 1024;
const header = (res, name) => { try { return decodeURIComponent(res.headers.get(name) || ""); } catch (e) { return ""; } };

/** A box on the page (PDF coordinates) as [x0, y0, x1, y1] on screen, within the page. */
function viewRect(viewport, [x0, y0, x1, y1]) {
  const [a, b] = viewport.convertToViewportPoint(x0, y0), [c, d] = viewport.convertToViewportPoint(x1, y1);
  return [a, b, c, d];
}

function sameBytes(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
const isTyping = (e) => /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) || e.target.isContentEditable;
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ } },
};

export class Reader {
  constructor(host, { token, isDesktop, api, tools = [], onEdit, onAllTools, onCommands, onHome } = {}) {
    this.host = host;
    this.token = token;
    this.isDesktop = isDesktop || (() => false);
    this.api = api || (() => null);
    this.tools = tools;
    this.onEdit = onEdit;
    this.onAllTools = onAllTools;
    this.onCommands = onCommands;
    this.onHome = onHome;
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
    this.titleEl = el("div", { class: "rd-title" });
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
    this.toolsBtn = el("button", { class: "rd-btn rd-tools-btn", type: "button", title: "Show the tools (T)", "aria-expanded": "false",
      html: svg("sliders") + "<span>Tools</span>" + svg("down") });
    // Changes not saved yet: Save and Undo come out where they're seen.
    this.saveNowBtn = el("button", { class: "rd-btn primary rd-savenow", type: "button", hidden: true,
      title: "Save your changes (Ctrl+S)", html: svg("save") + "<span>Save</span>" });
    this.undoNowBtn = b("undo", "Undo the last change (Ctrl+Z)", { hidden: true });
    this.homeBtn = this.onHome ? b("home", "Home: all of ZPDF's tools (your documents stay open)") : null;

    // The toolbar keeps what reading needs; everything else waits in the
    // tray under it until Tools pulls it out.
    const toolbar = el("div", { class: "rd-toolbar", role: "toolbar", "aria-label": "Reader" },
      el("div", { class: "rd-group" }, this.homeBtn, this.sidebarBtn),
      this.titleEl,
      el("div", { class: "rd-group" }, this.prevBtn, this.nextBtn,
        el("label", { class: "rd-page" }, this.pageInput, this.pageCount)),
      el("span", { class: "rd-sep" }),
      el("div", { class: "rd-group rd-zoomgroup" }, this.zoomOutBtn, this.zoomInput, this.zoomMenuBtn, this.zoomInBtn),
      el("span", { class: "rd-sep" }),
      this.undoNowBtn, this.saveNowBtn, this.scaleBtn, this.toolsBtn);

    // Tools tray
    const t = (name, label, title, attrs = {}) => el("button", { class: "rd-btn rd-tray-btn", type: "button", title, html: svg(name) + `<span>${label}</span>`, ...attrs });
    this.openBtn = t("open", "Open", "Open a PDF (Ctrl+O)");
    this.findBtn = t("search", "Find", "Find in document (Ctrl+F)", { "aria-pressed": "false" });
    this.annotateBtn = t("pen", "Annotate & sign", "Annotate and sign", { "aria-pressed": "false" });
    this.rotCcwBtn = b("ccw", "Rotate counter-clockwise (Shift+R)");
    this.rotCwBtn = b("cw", "Rotate clockwise (R)");
    this.printBtn = t("print", "Print", "Print (Ctrl+P)");
    this.saveBtn = t("save", "Save", "Save (Ctrl+S); Save as: Ctrl+Shift+S");
    this.moreBtn = b("more", "More", { "aria-haspopup": "menu", "aria-expanded": "false" });
    this.appToolsBtn = this.onAllTools ? t("grid", "All tools", "Show every ZPDF tool") : null;
    this.commandsBtn = this.onCommands ? t("command", "Commands", "Search every command and tool (Ctrl+K)") : null;
    this.tray = el("div", { class: "rd-tray", role: "toolbar", "aria-label": "Tools", hidden: true },
      this.openBtn, this.findBtn, this.annotateBtn,
      el("span", { class: "rd-sep" }), this.rotCcwBtn, this.rotCwBtn,
      el("span", { class: "rd-sep" }), this.printBtn, this.saveBtn, this.moreBtn,
      el("span", { style: "flex:1" }), this.commandsBtn, this.appToolsBtn);

    // Find bar
    this.findInput = el("input", { type: "search", placeholder: "Find in document", "aria-label": "Find in document", autocomplete: "off", spellcheck: "false" });
    this.findPrevBtn = b("up", "Previous match (Shift+Enter)");
    this.findNextBtn = b("down", "Next match (Enter)");
    this.findCase = el("input", { type: "checkbox" });
    this.findWord = el("input", { type: "checkbox" });
    this.findAll = el("input", { type: "checkbox", checked: true });
    this.findStatus = el("span", { class: "rd-findstatus", "aria-live": "polite" });
    this.findCloseBtn = b("x", "Close find bar (Esc)");
    this.findbar = el("div", { class: "rd-findbar", hidden: true },
      this.findInput, this.findPrevBtn, this.findNextBtn,
      el("label", {}, this.findAll, "Highlight all"),
      el("label", {}, this.findCase, "Match case"),
      el("label", {}, this.findWord, "Whole words"),
      this.findStatus, el("span", { style: "flex:1" }), this.findCloseBtn);

    // Annotation bar
    const tool = (name, label, mode) => el("button", { class: "rd-btn rd-tool", type: "button", title: label, "aria-pressed": "false", "data-mode": mode, html: svg(name) + `<span>${label}</span>` });
    this.annotTools = [
      tool("pointer", "Select", "NONE"), tool("highlight", "Highlight", "HIGHLIGHT"),
      tool("underline", "Underline", "UNDERLINE"), tool("strike", "Strike out", "STRIKEOUT"),
      tool("note", "Note", "NOTE"), tool("text", "Text", "FREETEXT"),
      tool("draw", "Draw", "INK"), tool("shapes", "Shapes", "SHAPE"), tool("stamp", "Stamp", "STAMPS"), tool("image", "Picture", "STAMP"),
      tool("edittext", "Edit text", "EDITTEXT"), tool("imgedit", "Pictures", "IMAGES"), tool("link", "Links", "LINKS"),
    ];
    const tip = (mode, title) => { this.annotTools.find((b) => b.dataset.mode === mode).title = title; };
    for (const mode of ["NONE", "UNDERLINE", "STRIKEOUT"]) this.annotTools.find((b) => b.dataset.mode === mode).classList.add("compact");
    tip("UNDERLINE", "Underline text: select the words (or select them first, then click here)");
    tip("STRIKEOUT", "Strike out text: select the words (or select them first, then click here)");
    tip("NOTE", "Add a note: click where it goes, then type your comment");
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
    this.annotTools.at(-1).title = "Change words on the page: click a line of text (E)";
    this.signBtn = el("button", { class: "rd-btn rd-tool", type: "button", title: "Add your signature", html: svg("sign") + "<span>Sign</span>" });
    this.certSignBtn = el("button", { class: "rd-btn rd-tool compact", type: "button", "data-mode": "CERTSIGN", "aria-pressed": "false",
      title: "Sign with a digital ID (a certificate, .pfx or .p12): drag a box where the signature goes", html: svg("badge") + "<span>Digital ID</span>" });
    this.annotTools.push(this.certSignBtn);
    this.#buildRedact();  // reader redaction
    this.swatches = COLORS.map(([name, ink, mark], i) => el("button", { class: "rd-swatch", type: "button", title: name, "aria-label": name, "aria-pressed": String(i === 1),
      style: `--ink:${ink};--mark:${mark}`, "data-index": i }));
    this.sizeSelect = el("select", { class: "rd-zoom", "aria-label": "Line thickness or text size" });
    this.undoBtn = b("undo", "Undo (Ctrl+Z)", { disabled: true });
    this.redoBtn = b("redo", "Redo (Ctrl+Y)", { disabled: true });
    this.deleteBtn = b("trash", "Delete the selected annotation (Delete)", { disabled: true });
    this.annotSaveBtn = el("button", { class: "rd-btn primary", type: "button", title: "Save your changes (Ctrl+S)", html: svg("save") + "<span>Save</span>" });
    this.annotCloseBtn = b("x", "Close the annotation tools");
    this.annotbar = el("div", { class: "rd-findbar rd-annotbar", hidden: true, role: "toolbar", "aria-label": "Annotate" },
      ...this.annotTools.filter((b) => b !== this.certSignBtn && b !== this.redactBtn), this.signBtn, this.certSignBtn, this.redactBtn,
      el("span", { class: "rd-sep" }),
      el("div", { class: "rd-swatches", role: "group", "aria-label": "Colour" }, ...this.swatches), this.shapeSelect, this.stampSelect, this.stampDatedLabel, this.sizeSelect,
      this.redactHow, this.redactApplyBtn,
      el("span", { class: "rd-sep" }), this.undoBtn, this.redoBtn, this.deleteBtn,
      el("span", { style: "flex:1" }), this.annotSaveBtn, this.annotCloseBtn);

    this.#buildScaleBar();

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
      input.addEventListener("change", () => { if (input.files[0]) this.#importComments(input.files[0]); });
      input.click();
    };
    const commentActions = el("div", { class: "rd-commentactions" },
      commentBtn("Summary", "A PDF listing every comment, page by page", () => this.#commentSummary()),
      commentBtn("Export", "Save the comments as an XFDF file, to send or to add to another copy", () => this.#exportComments()),
      commentBtn("Import…", "Add comments from an XFDF file (from Acrobat or another copy)", pickXfdf));
    // -- end of comment summary and XFDF --
    this.commentsPanel = el("div", { class: "rd-panel rd-commentspanel", role: "tabpanel", hidden: true },
      el("label", { class: "rd-authorrow" }, "Comment as", this.authorInput), commentActions, this.commentsList);
    this.thumbsList = el("ol", { class: "rd-thumbs", role: "listbox", "aria-multiselectable": "true", "aria-label": "Pages" });
    this.selected = new Set();
    this.outlineList = el("ul", { class: "rd-outline" });
    this.pageBar = el("div", { class: "rd-pagebar" });
    this.thumbsPanel = el("div", { class: "rd-panel", role: "tabpanel" }, this.pageBar, this.thumbsList);
    this.outlinePanel = el("div", { class: "rd-panel", role: "tabpanel", hidden: true }, this.outlineList);
    this.sidebar = el("aside", { class: "rd-sidebar", hidden: true, "aria-label": "Pages, bookmarks and comments" },
      el("div", { class: "rd-tabs", role: "tablist" }, this.thumbsTab, this.outlineTab, this.commentsTab),
      this.thumbsPanel, this.outlinePanel, this.commentsPanel);

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
        el("p", { class: "keys" }, "Ctrl+K commands · T tools · E edit text · M measure · Ctrl+F find · N / P pages")),
      this.recentBox = el("section", { class: "rd-recent", hidden: true, "aria-label": "Recent files" },
        el("h3", {}, "Recent"), this.recentList = el("ul", {})));
    this.main = el("div", { class: "rd-main" }, this.container, this.loading, this.empty, this.measPanel);
    this.body = el("div", { class: "rd-body" }, this.sidebar, this.main);

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
    formClose.addEventListener("click", () => { this.formbar.hidden = true; if (this.activeTab) this.activeTab.formHint = "hidden"; });
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
    this.root.append(toolbar, this.tray, this.tabbar, this.findbar, this.annotbar, this.scalebar, this.overlaybar, this.sigbar, this.formbar, this.body, this.menu, this.ctxMenu, this.zoomMenu, this.fileInput);
    // A drawing in progress takes every click in the window as the next
    // stroke; end it first when the tabs, toolbar or menu are used. This
    // listens on the window before pdf.js does, so it runs first.
    const outside = [this.tabbar, toolbar, this.tray, this.scalebar, this.menu, this.ctxMenu];
    window.addEventListener("pointerdown", (e) => {
      if (!outside.some((area) => area.contains(e.target))) return;
      this.#finishEditing();
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
    const items = [
      this.#menuItem("spread", "Two-page view", () => this.toggleSpread(), spread),
      this.#menuItem("hand", "Hand tool (drag to scroll)", () => this.toggleHand(), this.container.classList.contains("hand")),
      this.#menuItem("moon", "Dark pages", () => this.toggleDarkPages(), this.container.classList.contains("dark")),
      el("hr"),
      this.#menuItem("full", "Full screen", () => this.toggleFullscreen()),
      this.#menuItem("print", "Print…", () => this.print()),
      ...(this.activeTab?.fileId ? [this.#menuItem("save", "Save", () => this.save())] : []),
      this.#menuItem("save", this.activeTab?.fileId ? "Save as…" : this.#edited() ? "Save a copy with your changes…" : "Save a copy…", () => this.saveCopy()),
      this.#menuItem("layers", this.overlay ? "Stop comparing revisions" : "Compare with another revision…", () => (this.overlay ? this.closeCompare() : this.compareRevision())),
      this.#menuItem("badge", "Sign with a digital ID…", () => this.useTool("CERTSIGN")),
      this.#menuItem("fillable", "Make it fillable (find the form's fields)", () => this.makeFillable()),
      this.#menuItem("info", "Document properties", () => this.showProperties()),
      this.#menuItem("access", "Check accessibility", () => this.checkAccessibility()),  // -- accessibility --
      this.#menuItem("x", "Close this document", () => this.closeTab()),
    ];
    if (this.onEdit && this.tools.length) {
      items.push(el("hr"), el("div", { class: "label" }, "Edit with"));
      for (const [id, label] of this.tools) items.push(this.#menuItem("tool", label, () => this.#edit(id)));
    }
    for (const item of items) if (item.tagName === "BUTTON") item.disabled = !has;
    this.menu.replaceChildren(...items);
  }

  #toggleMenu() {
    if (!this.menu.hidden) return this.#closeMenu();
    this.#renderMenu();
    // Open under the More button, wherever the tray put it.
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
        if (value === "true-size") this.#viewAt("true");
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
    this.openBtn.addEventListener("click", () => this.chooseFile());
    this.emptyOpenBtn.addEventListener("click", () => this.chooseFile());
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
    this.rotCwBtn.addEventListener("click", () => this.rotate(90));
    this.rotCcwBtn.addEventListener("click", () => this.rotate(-90));
    this.findBtn.addEventListener("click", () => (this.findbar.hidden ? this.openFind() : this.closeFind()));
    this.moreBtn.addEventListener("click", () => this.#toggleMenu());
    this.toolsBtn.addEventListener("click", () => this.toggleTray());
    this.saveNowBtn.addEventListener("click", () => this.save());
    this.undoNowBtn.addEventListener("click", () => this.#undoLast());
    this.homeBtn?.addEventListener("click", () => this.onHome());
    this.printBtn.addEventListener("click", () => this.print());
    this.saveBtn.addEventListener("click", () => this.save());
    if (this.appToolsBtn) this.appToolsBtn.addEventListener("click", () => this.onAllTools());
    if (this.commandsBtn) this.commandsBtn.addEventListener("click", () => this.onCommands());
    document.addEventListener("pointerdown", (e) => { if (!this.menu.hidden && !this.menu.contains(e.target) && e.target !== this.moreBtn && !this.moreBtn.contains(e.target)) this.#closeMenu(); });
    this.thumbsTab.addEventListener("click", () => this.#showTab("thumbs"));
    this.outlineTab.addEventListener("click", () => this.#showTab("outline"));
    this.commentsTab.addEventListener("click", () => this.#showTab("comments"));
    this.authorInput.addEventListener("change", () => store.set("pdfm-author", this.authorInput.value.trim()));
    this.shapeSelect.addEventListener("change", () => store.set("pdfm-shape", this.shapeSelect.value));

    // Find
    this.findInput.addEventListener("input", () => this.#find(""));
    this.findInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this.#find("again", e.shiftKey); }
      if (e.key === "Escape") { e.preventDefault(); this.closeFind(); }
    });
    this.findNextBtn.addEventListener("click", () => this.#find("again", false));
    this.findPrevBtn.addEventListener("click", () => this.#find("again", true));
    this.findCase.addEventListener("change", () => this.#find("casesensitivitychange"));
    this.findWord.addEventListener("change", () => this.#find("entirewordchange"));
    this.findAll.addEventListener("change", () => this.#find("highlightallchange"));
    this.findCloseBtn.addEventListener("click", () => this.closeFind());

    // Annotating
    this.annotateBtn.addEventListener("click", () => (this.annotbar.hidden ? this.openAnnotate() : this.closeAnnotate()));
    for (const btn of this.annotTools) btn.addEventListener("click", () => this.#setEditMode(btn.dataset.mode, btn.dataset.mode === "STAMP"));
    this.signBtn.addEventListener("click", () => this.sign());
    for (const sw of this.swatches) sw.addEventListener("click", () => this.#setColor(+sw.dataset.index));
    this.sizeSelect.addEventListener("change", () => this.#setSize(+this.sizeSelect.value));
    this.undoBtn.addEventListener("click", () => (this.textEditMode || this.markupMode ? this.#undoTextEdit() : this.uiManager?.undo()));
    this.redoBtn.addEventListener("click", () => this.uiManager?.redo());
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
    this.container.addEventListener("pointerdown", (e) => this.#measureDown(e), true);
    this.container.addEventListener("pointermove", (e) => this.#measureMove(e));
    window.addEventListener("pointerup", () => this.#measureUp());

    // Edit text tool: click a line of text to change it.
    this.container.addEventListener("click", (e) => this.#textClick(e), true);

    // Notes and shapes: click or drag on a page; underline and strike out: select words.
    this.container.addEventListener("pointerdown", (e) => this.#markupDown(e), true);
    this.container.addEventListener("pointermove", (e) => this.#markupMove(e));
    window.addEventListener("pointerup", (e) => this.#markupUp(e));

    document.addEventListener("keydown", (e) => this.#onKey(e));
  }

  #onKey(e) {
    if (!this.active || document.querySelector("dialog[open]")) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "o") { e.preventDefault(); this.chooseFile(); return; }
    if (e.key === "Escape" && !this.menu.hidden) { this.#closeMenu(); this.moreBtn.focus(); return; }
    if (e.key === "Escape" && !this.zoomMenu.hidden) { this.#closeZoomMenu(); this.zoomMenuBtn.focus(); return; }
    if (!this.doc) return;
    if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); this.openFind(); return; }
    if (mod && e.key.toLowerCase() === "p") { e.preventDefault(); this.print(); return; }
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); if (e.shiftKey) this.saveCopy(); else this.save(); return; }
    if (mod && !isTyping(e) && !this.scalebar.hidden && (e.key.toLowerCase() === "z" || e.key.toLowerCase() === "y")
        && this.#measureUndo(e.key.toLowerCase() === "y" || e.shiftKey)) { e.preventDefault(); return; }
    if (mod && e.key.toLowerCase() === "z" && !e.shiftKey && !isTyping(e) && this.activeTab?.undo?.length
        && (this.textEditMode || this.markupMode || this.annotbar.hidden)) { e.preventDefault(); this.#undoTextEdit(); return; }
    if (e.key === "Escape" && !this.ctxMenu.hidden) { this.#closeContextMenu(); return; }
    if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); this.pdfViewer.increaseScale(); return; }
    if (mod && e.key === "-") { e.preventDefault(); this.pdfViewer.decreaseScale(); return; }
    if (mod && e.key === "0") { e.preventDefault(); this.pdfViewer.currentScaleValue = "auto"; return; }
    if (mod || e.altKey || isTyping(e)) return;
    if (this.measureMode && this.#measureKey(e)) { e.preventDefault(); return; }
    if (e.key === "Escape" && !this.findbar.hidden) { this.closeFind(); return; }
    if (this.markupMode === "IMAGES" && this.pickedImage && !isTyping(e) && (e.key === "Delete" || e.key === "Backspace")) {
      e.preventDefault();
      this.#editImage("delete");
      return;
    }
    if (e.key === "Escape" && this.markupMode === "IMAGES" && this.pickedImage) { this.#pickImage(null); return; }
    if (e.key === "Escape" && this.editMode && this.editMode !== "NONE") { this.#setEditMode("NONE"); return; }
    if (e.key === "Escape" && !this.tray.hidden) { this.toggleTray(false); return; }
    const v = this.pdfViewer;
    switch (e.key) {
      case "n": case "j": v.nextPage(); break;
      case "p": case "k": v.previousPage(); break;
      case "r": this.rotate(90); break;
      case "R": this.rotate(-90); break;
      case "t": this.toggleTray(); break;
      case "e": this.useTool(this.textEditMode ? "NONE" : "EDITTEXT"); break;
      case "m": this.measureTool(this.measureMode === "distance" ? null : "distance"); break;
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
    if (this.doc) this.container.focus({ preventScroll: true }); else this.refreshRecent();
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
  /** Fill the start screen's Recent list: files opened from this computer by
   *  path (desktop window, "Open with") and by browser handle. */
  async refreshRecent() {
    let server = [];
    try {
      const res = await fetch("/api/recent", { headers: { "X-Pdfm-Token": this.token } });
      if (res.ok) server = (await res.json()).files;
    } catch (e) { /* offline or an old server */ }
    const handles = files.canOpen ? await files.recentHandles() : [];
    const entries = [
      ...server.map((f) => ({ name: f.name, where: f.folder, path: f.path, opened: (f.opened || 0) * 1000,
        open: () => this.openById(f.id), forget: () => this.#forgetRecent({ path: f.path }) })),
      ...handles.map((h) => ({ name: h.name, where: "", opened: h.opened,
        open: async () => {
          const file = await files.fileFromHandle(h.handle);
          if (!file) { this.toast(`“${h.name}” can't be opened any more. It may have been moved or deleted.`); return; }
          files.rememberHandle(h.handle);
          await this.openFile(file);
        },
        forget: async () => { await files.forgetHandle(h.key); this.refreshRecent(); } })),
    ].sort((a, b) => b.opened - a.opened).slice(0, 15);
    const when = (t) => {
      if (!t) return "";
      const d = new Date(t), today = new Date();
      const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
      const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      return days === 0 ? `Today ${time}` : days === 1 ? `Yesterday ${time}` : d.toLocaleDateString();
    };
    this.recentList.replaceChildren(...entries.map((e) => el("li", {},
      el("button", { type: "button", class: "rd-recent-open", title: e.path || e.name, onclick: () => e.open() },
        el("span", { class: "rd-recent-icon", html: svg("book") }),
        el("span", { class: "rd-recent-text" }, el("strong", {}, e.name), el("small", {}, [e.where, when(e.opened)].filter(Boolean).join(" · ")))),
      el("button", { type: "button", class: "rd-btn rd-recent-x", title: "Remove from Recent", "aria-label": `Remove ${e.name} from Recent`, html: svg("x"), onclick: () => e.forget() }))));
    this.recentBox.hidden = !entries.length;
  }

  async #forgetRecent(data) {
    try {
      await fetch("/api/recent/forget", { method: "POST", headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/json" }, body: JSON.stringify(data) });
    } catch (e) { /* ignore */ }
    this.refreshRecent();
  }

  /** Open a file the server knows by id; ``goto`` = {page, find} shows a
   *  page and searches for text on it (e.g. from the folder search). */
  async openById(id, goto = null) {
    this.#busy(true);
    try {
      const url = "/api/file/" + encodeURIComponent(id), headers = { "X-Pdfm-Token": this.token };
      // The first byte tells the file's size, place and version.
      const probe = await fetch(url, { headers: { ...headers, Range: "bytes=0-0" } });
      if (!probe.ok && probe.status !== 416) throw new Error((await probe.json().catch(() => ({}))).error || probe.statusText);
      await probe.arrayBuffer();
      const name = header(probe, "X-Pdfm-Filename") || "document.pdf";
      const file = { fileId: id, path: header(probe, "X-Pdfm-Path"), stamp: probe.headers.get("X-Pdfm-Stamp") || "",
        size: Number((probe.headers.get("Content-Range") || "").split("/")[1]) || 0 };
      if (file.size > LAZY_BYTES) { await this.#serial(() => this.#openBytesNow(null, name, null, goto, { ...file, url })); return; }
      const res = await fetch(url, { headers });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      file.stamp = res.headers.get("X-Pdfm-Stamp") || file.stamp;
      await this.openBytes(new Uint8Array(await res.arrayBuffer()), name, null, goto, file);
    } catch (e) {
      this.toast("Could not open the file: " + (e.message || e));
    } finally {
      this.#busy(false);
    }
  }

  /** Run ``fn`` after the document changes started before it. */
  #serial(fn) {
    const run = this.chain.then(() => fn());
    this.chain = run.catch(() => {});
    return run;
  }

  async openFile(file, password = null) {
    await this.openBytes(new Uint8Array(await file.arrayBuffer()), file.name || "document.pdf", password);
  }

  /** Open a PDF in a new tab, or switch to its tab if it's already open. */
  openBytes(bytes, name, password = null, goto = null, file = null) {
    return this.#serial(() => this.#openBytesNow(bytes, name, password, goto, file));
  }

  /** ``file``: where a document opened from this computer lives (fileId,
   *  path, stamp, size; url when it's read in pieces, bytes then null). */
  async #openBytesNow(bytes, name, password, goto, file = null) {
    const same = this.tabs.find((t) => (file?.path && t.path === file.path)
      || (bytes && t.original && t.name === name && sameBytes(t.original, bytes)));
    if (same) {
      if (same === this.activeTab) { if (goto) this.#goto(goto); }
      else { if (goto) same.goto = goto; await this.#switchToNow(same); }  // applied once its pages are there
      return;
    }
    const previous = this.activeTab;
    await this.#stash();
    // edited: bytes holds changes made here; dirty: changes not saved to a file
    // yet; version: counts every change, so a save knows what it included.
    const tab = { id: ++tabSeq, original: bytes, bytes, name, password, edited: false, dirty: false, version: 0, goto, ...file };
    this.tabs.push(tab);
    if (!(await this.#load(tab))) {
      this.tabs.splice(this.tabs.indexOf(tab), 1);
      if (!(previous && this.tabs.includes(previous) && await this.#load(previous))) {
        await this.#closeDocument();
        this.#showEmpty();
      }
    }
    this.#renderTabs();
  }

  /** Keep the open tab's unsaved annotations and form entries in its bytes
   *  before another document takes its place in the viewer. */
  async #stash() {
    const tab = this.activeTab;
    this.#finishEditing();
    if (!tab || !this.doc || !this.#changed()) return;
    tab.bytes = await this.doc.saveDocument();
    tab.edited = true;
  }

  #switchTo(tab) {
    return this.#serial(() => this.#switchToNow(tab));
  }

  async #switchToNow(tab) {
    if (tab === this.activeTab || !this.tabs.includes(tab)) return;
    await this.#stash();
    if (!(await this.#load(tab))) { await this.#closeDocument(); this.#showEmpty(); }
    this.#renderTabs();
  }

  closeTab(tab = this.activeTab) {
    return this.#serial(() => this.#closeTabNow(tab));
  }

  async #closeTabNow(tab) {
    if (!tab || !this.tabs.includes(tab)) return;
    if (tab.dirty && !confirm(`Close “${tab.name}” without saving your changes?`)) return;
    const i = this.tabs.indexOf(tab);
    this.tabs.splice(i, 1);
    if (tab === this.activeTab) {
      this.activeTab = null;
      const next = this.tabs[i] || this.tabs[i - 1];
      if (!next || !(await this.#load(next))) { await this.#closeDocument(); this.#showEmpty(); }
    }
    this.#renderTabs();
  }

  /** Any tab with changes that haven't been saved to a file? */
  hasUnsavedChanges() {
    return this.tabs.some((t) => t.dirty);
  }

  /** Show the tab bar. Tabs keep their elements (a click mustn't land on an
   *  element that was replaced halfway through it). */
  #renderTabs() {
    this.tabbar.hidden = this.tabs.length < 2;
    this.tabEls ||= new Map();
    for (const [id, parts] of this.tabEls) if (!this.tabs.some((t) => t.id === id)) { parts.wrap.remove(); this.tabEls.delete(id); }
    if (!this.addTabBtn) {
      this.addTabBtn = el("button", { type: "button", class: "rd-btn rd-doctab-add", title: "Open another PDF", "aria-label": "Open another PDF", html: svg("plus") });
      this.addTabBtn.addEventListener("click", () => this.chooseFile());
    }
    const order = this.tabs.map((tab) => {
      let parts = this.tabEls.get(tab.id);
      if (!parts) {
        const dot = el("span", { class: "rd-dot", title: "Unsaved changes" });
        const label = el("span", { class: "rd-doctab-label" }, tab.name);
        const btn = el("button", { type: "button", class: "rd-doctab-name", role: "tab", title: tab.name }, dot, label);
        btn.addEventListener("click", () => this.#switchTo(tab));
        const close = el("button", { type: "button", class: "rd-doctab-x", title: "Close", "aria-label": `Close ${tab.name}`, html: svg("x") });
        close.addEventListener("click", () => this.closeTab(tab));
        const wrap = el("div", { class: "rd-doctab" }, btn, close);
        wrap.addEventListener("auxclick", (e) => { if (e.button === 1) { e.preventDefault(); this.closeTab(tab); } });
        parts = { wrap, btn, dot };
        this.tabEls.set(tab.id, parts);
      }
      const active = tab === this.activeTab;
      parts.wrap.classList.toggle("active", active);
      parts.btn.setAttribute("aria-selected", String(active));
      parts.dot.hidden = !tab.dirty;
      return parts.wrap;
    });
    this.#showSaveState();
    // Re-append only if the order changed, so elements stay where they are.
    const current = [...this.tabbar.children].filter((n) => n !== this.addTabBtn);
    if (current.length !== order.length || current.some((n, i) => n !== order[i])) this.tabbar.replaceChildren(...order, this.addTabBtn);
    else if (!this.addTabBtn.isConnected) this.tabbar.append(this.addTabBtn);
  }

  #showEmpty() {
    this.activeTab = null;
    this.#busy(false);
    this.empty.hidden = false;
    this.titleEl.textContent = "";
    this.pageInput.value = "";
    this.pageCount.textContent = "";
    for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
      this.zoomInput, this.zoomMenuBtn, this.rotCcwBtn, this.rotCwBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = true;
    this.zoomInput.value = "";
    this.sidebar.hidden = true;
    this.root.classList.remove("reading");
    if (this.active) document.title = "PDF reader — ZPDF";
    this.refreshRecent();
  }

  /** Show ``tab``'s document in the viewer. Returns false if it couldn't be
   *  opened (not a PDF, or the password prompt was cancelled). */
  async #load(tab) {
    this.#busy(true);
    try {
      await loadPdfjs();
      this.#ensureViewer();
      await this.#closeDocument();
      this.bytes = tab.bytes;
      this.name = tab.name;
      this.password = tab.password;
      const task = await openDocument({
        ...DOC_OPTIONS,
        ...(tab.bytes
          ? { data: tab.bytes.slice() }  // pdf.js takes ownership of the buffer; keep our copy intact
          : { url: tab.url, httpHeaders: { "X-Pdfm-Token": this.token }, length: tab.size || undefined,
              rangeChunkSize: 1 << 20, disableAutoFetch: true, disableStream: true }),
        password: tab.password || undefined,
        enableXfa: true,
        fontExtraProperties: true,  // fonts' own names, to find the installed copy when editing text
      });
      task.onPassword = (update, reason) => {
        this.#busy(false);
        this.#askPassword(reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD).then((pw) => {
          if (pw === null) { destroyTask(task); return; }
          this.password = tab.password = pw;
          this.#busy(true);
          update(pw);
        });
      };
      this.task = task;
      const doc = await task.promise;
      this.doc = doc;
      this.activeTab = tab;
      doc.annotationStorage.onSetModified = () => {
        tab.version = (tab.version || 0) + 1;
        if (!tab.dirty) { tab.dirty = true; this.#renderTabs(); }
      };
      this.pdfViewer.setDocument(doc);
      this.linkService.setDocument(doc, null);
      this.scripting.setDocument(doc).catch(() => {});  // a form without scripts works as before
      // (The viewer hands the document to the find controller itself once the
      // first page is drawn; doing it here too would reset a search started
      // when the pages appear, e.g. from the folder search.)
      this.titleEl.textContent = tab.name;
      this.titleEl.title = tab.name;
      document.title = tab.name + " — ZPDF";
      this.empty.hidden = true;
      for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
        this.zoomInput, this.zoomMenuBtn, this.rotCcwBtn, this.rotCwBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = false;
      this.pageCount.textContent = `of ${doc.numPages}`;
      this.root.classList.add("reading");
      this.toggleTray(store.get("pdfm-reader-tray", false), false);
      this.#setSidebar(store.get("pdfm-reader-sidebar", false));
      this.#buildThumbs();
      this.#buildOutline();
      this.#listComments();
      this.#loadMeasures();
      this.#checkSignatures();
      this.#checkForm();
      doc.getPageLabels().then((labels) => {
        if (doc !== this.doc || !labels) return;
        // Only use labels that differ from plain page numbers.
        if (labels.some((l, i) => l !== String(i + 1))) { this.labels = labels; this.pdfViewer.setPageLabels(labels); this.#updatePage(); }
      }).catch(() => {});
      return true;
    } catch (e) {
      await this.#closeDocument();
      if (!(e && e.name === "PasswordException")) {  // not just cancelled at the password prompt
        this.toast(e && e.name === "InvalidPDFException" ? `“${tab.name}” isn't a valid PDF.` : "Could not open the PDF: " + (e.message || e));
      }
      return false;
    } finally {
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
      if (Math.abs(width - lastW) < 4 && Math.abs(height - lastH) < 100) return;
      lastW = width;
      lastH = height;
      cancelAnimationFrame(refit);
      refit = requestAnimationFrame(() => {
        const value = this.pdfViewer.currentScaleValue;
        if (this.doc && ["auto", "page-width", "page-fit", "page-height"].includes(value)) this.pdfViewer.currentScaleValue = value;
      });
    }).observe(this.main);
    eventBus.on("annotationeditoruimanager", ({ uiManager }) => { this.uiManager = uiManager; this.#applyAnnotParams(); });
    // pdf.js asks to switch tools itself, e.g. after a picture is pasted.
    eventBus.on("switchannotationeditormode", ({ mode }) => this.#setEditMode(this.#modeName(mode)));
    eventBus.on("annotationeditormodechanged", ({ mode }) => this.#showEditMode(this.#modeName(mode)));
    eventBus.on("editingstateschanged", ({ details }) => {
      this.annotUndo = !!details.hasSomethingToUndo;
      this.#showSaveState();
      this.undoBtn.disabled = !details.hasSomethingToUndo;
      this.redoBtn.disabled = !details.hasSomethingToRedo;
      this.deleteBtn.disabled = !details.hasSelectedEditor;
    });

    eventBus.on("pagesinit", () => {
      const saved = store.get("pdfm-pos:" + this.#key(), null);
      this.pdfViewer.currentScaleValue = saved?.scale || "auto";
      if (saved?.page > 1 && saved.page <= this.doc.numPages) this.pdfViewer.currentPageNumber = saved.page;
      if (saved?.rotation) this.pdfViewer.pagesRotation = saved.rotation;
      const goto = this.activeTab?.goto;
      if (goto) { this.activeTab.goto = null; this.#goto(goto); }
      this.#updatePage();
      this.container.focus({ preventScroll: true });
    });
    eventBus.on("pagechanging", ({ pageNumber }) => { this.#updatePage(); this.#remember(); this.#refreshScale(); if (this.measureMode) this.#geometry(pageNumber); });
    eventBus.on("pagesloaded", () => this.#refreshScale());
    eventBus.on("annotationlayerrendered", ({ source }) => this.#nameFields(source?.div?.querySelector(".annotationLayer")));
    eventBus.on("pagerendered", ({ pageNumber }) => {
      this.#drawMeasures(pageNumber);
      if (this.markupMode === "IMAGES" || this.markupMode === "LINKS") this.#drawObjectBoxes(pageNumber);
      this.#drawRedactions(pageNumber);
      if (this.overlay) this.overlay.draw(this.pdfViewer.getPageView(pageNumber - 1), pageNumber).catch(() => {});
    });
    eventBus.on("scalechanging", ({ scale, presetValue }) => {
      const value = presetValue || String(scale);
      const preset = ZOOM_PRESETS.find(([v]) => v === value || (!presetValue && Math.abs(parseFloat(v) - scale) < 0.001));
      if (!presetValue && Math.abs(scale - this.#trueZoom()) < 0.001) this.zoomPreset = "true-size";
      else this.zoomPreset = preset ? preset[0] : "custom";
      this.#showZoom(scale);
      this.#syncViewAt();
      this.#remember();
      requestAnimationFrame(() => this.#drawAllMeasures());
    });
    eventBus.on("rotationchanging", () => { this.#remember(); requestAnimationFrame(() => this.#drawAllMeasures()); });
    eventBus.on("updatefindmatchescount", ({ matchesCount }) => this.#findResult(null, matchesCount));
    eventBus.on("updatefindcontrolstate", ({ state, matchesCount }) => this.#findResult(state, matchesCount));
  }

  async #closeDocument() {
    this.closeFind();
    await this.closeCompare();
    this.closeAnnotate();
    this.measureTool(null);
    this.uiManager = null;
    this.annotUndo = false;
    this.labels = null;
    if (this.thumbObserver) { this.thumbObserver.disconnect(); this.thumbObserver = null; }
    this.thumbsList.replaceChildren();
    this.outlineList.replaceChildren();
    const doc = this.doc, task = this.task;
    this.doc = null;
    this.task = null;
    this.#showSaveState();
    if (doc) {
      this.pdfViewer.setDocument(null);
      this.linkService.setDocument(null);
      this.findController.setDocument(null);
      this.scripting?.setDocument(null).catch(() => {});
      await destroyTask(doc.loadingTask || task);  // pdf.js frees a document through its task
    } else if (task) {
      await destroyTask(task);  // e.g. cancelled at the password prompt: wait for it to be gone
    }
  }

  #key() { return this.doc ? this.doc.fingerprints[0] : ""; }
  #remember() {
    if (!this.doc) return;
    store.set("pdfm-pos:" + this.#key(), {
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
    for (const li of this.thumbsList.children) li.classList.toggle("current", +li.dataset.page === n);
    const current = this.thumbsList.children[n - 1];
    if (current && !this.sidebar.hidden) {
      const panel = this.thumbsPanel, top = current.offsetTop, bottom = top + current.offsetHeight;
      if (top < panel.scrollTop || bottom > panel.scrollTop + panel.clientHeight) panel.scrollTop = top - panel.clientHeight / 3;
    }
  }

  #goToTypedPage() {
    const v = this.pdfViewer, text = this.pageInput.value.trim();
    if (this.labels && this.labels.includes(text)) { v.currentPageLabel = text; return; }
    const n = parseInt(text, 10);
    if (n >= 1 && n <= v.pagesCount) v.currentPageNumber = n;
    else this.#updatePage();
  }

  rotate(delta) { this.pdfViewer.pagesRotation = (this.pdfViewer.pagesRotation + delta + 360) % 360; this.#rebuildThumbsSoon(); }

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

  /** Pull the tools tray out (or put it away); ``open`` defaults to the other way. */
  toggleTray(open = this.tray.hidden, remember = true) {
    if (!open) this.#closeMenu();
    this.tray.hidden = !open;
    this.toolsBtn.setAttribute("aria-expanded", String(open));
    this.toolsBtn.title = open ? "Hide the tools (T)" : "Show the tools (T)";
    if (remember) store.set("pdfm-reader-tray", open);
  }

  // ---- sidebar: thumbnails and bookmarks ----------------------------------------
  toggleSidebar() { this.#setSidebar(this.sidebar.hidden); store.set("pdfm-reader-sidebar", !this.sidebar.hidden); }
  #setSidebar(open) {
    this.sidebar.hidden = !open;
    this.sidebarBtn.setAttribute("aria-pressed", String(open));
    if (open) this.#updatePage();
  }
  #showTab(which) {
    for (const [name, tab, panel] of [["thumbs", this.thumbsTab, this.thumbsPanel], ["outline", this.outlineTab, this.outlinePanel],
      ["comments", this.commentsTab, this.commentsPanel]]) {
      tab.setAttribute("aria-selected", String(name === which));
      panel.hidden = name !== which;
    }
    if (which === "comments") this.#listComments();
  }

  async #buildThumbs() {
    const doc = this.doc;
    const first = await doc.getPage(1);
    if (doc !== this.doc) return;
    const vp = first.getViewport({ scale: 1, rotation: this.pdfViewer?.pagesRotation || 0 });
    const ratio = vp.height / vp.width;
    this.thumbQueue = [];
    this.thumbObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) if (entry.isIntersecting && !entry.target.dataset.rendered) this.#queueThumb(entry.target);
    }, { root: this.thumbsPanel, rootMargin: "300px 0px" });
    const items = [];
    for (let n = 1; n <= doc.numPages; n++) {
      const box = el("div", { class: "rd-thumb-img", style: `height:${Math.round(128 * ratio)}px` });
      const actions = el("div", { class: "rd-thumb-actions" },
        el("button", { type: "button", class: "rd-thumb-act", "data-act": "rotate", title: "Turn clockwise", "aria-label": `Turn page ${n} clockwise`, html: svg("cw") }),
        el("button", { type: "button", class: "rd-thumb-act", "data-act": "delete", title: "Delete page", "aria-label": `Delete page ${n}`, html: svg("trash") }));
      const li = el("li", { class: "rd-thumb", "data-page": n, tabindex: "0", role: "option", draggable: "true",
        "aria-selected": "false", "aria-label": `Page ${n}` }, box, actions, el("span", {}, String(n)));
      items.push(li);
    }
    this.selected = new Set();
    this.thumbsList.replaceChildren(...items);
    this.#renderPageBar();
    for (const li of items) this.thumbObserver.observe(li);
    this.#updatePage();
  }

  // ---- thumbnails: pick, drag to move, drop a PDF to insert ---------------------------------
  #select(pages, anchor = pages.at(-1)) {
    this.selected = new Set(pages);
    this.selectAnchor = anchor;
    for (const li of this.thumbsList.children) li.setAttribute("aria-selected", String(this.selected.has(+li.dataset.page)));
    this.#renderPageBar();
  }

  #renderPageBar() {
    const pages = [...this.selected].sort((a, b) => a - b);
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
      if (act === "rotate") return this.rotatePages(this.selected.has(n) ? [...this.selected] : [n], 90);
      if (act === "delete") return this.deletePages(this.selected.has(n) && this.selected.size > 1 ? [...this.selected] : [n]);
      if (e.shiftKey && this.selectAnchor) {
        const [a, z] = [Math.min(this.selectAnchor, n), Math.max(this.selectAnchor, n)];
        this.#select(Array.from({ length: z - a + 1 }, (_, i) => a + i), this.selectAnchor);
      } else if (e.ctrlKey || e.metaKey) {
        const next = new Set(this.selected);
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
      if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); this.deletePages(this.selected.has(n) ? [...this.selected] : [n]); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = list.children[n - 1 + (e.key === "ArrowDown" ? 1 : -1)];
        next?.focus();
      }
    });
    // Dragging pages: a line shows where they'll go.
    let marker = null;
    const clear = () => { if (marker) marker.classList.remove("drop-before", "drop-after"); marker = null; };
    const spot = (e) => {
      const li = e.target.closest?.(".rd-thumb");
      if (!li) {
        const last = list.lastElementChild;
        return last ? { li: last, before: Number(last.dataset.page) + 1, after: true } : null;
      }
      const r = li.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2;
      return { li, before: Number(li.dataset.page) + (after ? 1 : 0), after };
    };
    const isFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
    list.addEventListener("dragstart", (e) => {
      const n = pageOf(e);
      if (!n) return;
      if (!this.selected.has(n)) this.#select([n]);
      e.dataTransfer.setData("application/x-zpdf-pages", JSON.stringify([...this.selected]));
      e.dataTransfer.effectAllowed = "move";
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
      marker.classList.toggle("drop-after", where.after);
      marker.classList.toggle("drop-before", !where.after);
    });
    list.addEventListener("dragleave", (e) => { if (!list.contains(e.relatedTarget)) clear(); });
    list.addEventListener("dragend", clear);
    list.addEventListener("drop", (e) => {
      const where = spot(e);
      clear();
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

  #queueThumb(li) {
    li.dataset.rendered = "1";
    this.thumbQueue.push(li);
    if (!this.thumbBusy) this.#drainThumbs();
  }

  async #drainThumbs() {
    this.thumbBusy = true;
    const doc = this.doc;
    while (this.thumbQueue.length && doc === this.doc) {
      const li = this.thumbQueue.shift();
      try {
        const page = await doc.getPage(+li.dataset.page);
        const vp = page.getViewport({ scale: 1, rotation: (page.rotate + this.pdfViewer.pagesRotation) % 360 });
        const scale = (128 * (window.devicePixelRatio || 1)) / vp.width;
        const viewport = page.getViewport({ scale, rotation: (page.rotate + this.pdfViewer.pagesRotation) % 360 });
        const canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
        await page.render({ canvas, viewport, annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS }).promise;
        if (doc !== this.doc) break;
        const box = li.firstChild;
        box.style.height = Math.round(128 * viewport.height / viewport.width) + "px";
        box.replaceChildren(canvas);
      } catch (e) { /* skip pages that fail to render */ }
    }
    this.thumbBusy = false;
  }

  #rebuildThumbsSoon() {
    clearTimeout(this.thumbTimer);
    this.thumbTimer = setTimeout(() => {
      if (!this.doc) return;
      if (this.thumbObserver) this.thumbObserver.disconnect();
      this.#buildThumbs();
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
        const twisty = el("button", { class: "twisty", type: "button", "aria-expanded": String(open), "aria-label": "Expand", html: `<svg viewBox="0 0 24 24">${ICON.twisty}</svg>` });
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
  #goto({ page, find, scale, top, left } = {}) {
    if (scale) this.pdfViewer.currentScaleValue = scale;
    if (page && page <= this.pdfViewer.pagesCount) this.pdfViewer.currentPageNumber = page;
    if (top != null) { this.container.scrollTop = top; this.container.scrollLeft = left || 0; }
    if (find) {
      this.openFind();
      this.findInput.value = find;
      this.#find("");
    }
  }

  // ---- find -----------------------------------------------------------------
  openFind() {
    this.findbar.hidden = false;
    this.findBtn.setAttribute("aria-pressed", "true");
    const selected = String(window.getSelection() || "").trim();
    if (selected && selected.length < 100 && !selected.includes("\n")) this.findInput.value = selected;
    this.findInput.focus();
    this.findInput.select();
    if (this.findInput.value) this.#find("");
  }
  closeFind() {
    if (this.findbar.hidden) return;
    this.findbar.hidden = true;
    this.findBtn.setAttribute("aria-pressed", "false");
    if (this.eventBus) this.eventBus.dispatch("findbarclose", { source: this });
    this.container.focus({ preventScroll: true });
  }
  #find(type, previous = false) {
    if (!this.eventBus) return;
    this.eventBus.dispatch("find", {
      source: this, type, query: this.findInput.value,
      caseSensitive: this.findCase.checked, entireWord: this.findWord.checked,
      highlightAll: this.findAll.checked, findPrevious: previous, matchDiacritics: false,
    });
  }
  #findResult(state, matchesCount) {
    const { FindState } = viewer;
    const query = this.findInput.value;
    this.findInput.classList.toggle("notfound", state === FindState.NOT_FOUND && !!query);
    if (!query) { this.findStatus.textContent = ""; return; }
    if (state === FindState.NOT_FOUND) { this.findStatus.textContent = "No matches"; return; }
    if (state === FindState.PENDING) { this.findStatus.textContent = "Searching…"; return; }
    const { current = 0, total = 0 } = matchesCount || {};
    if (total) this.findStatus.textContent = `${current || "–"} of ${total}${total >= 1000 ? "+" : ""}`;
    if (state === FindState.WRAPPED) this.toast("Reached the end — continued from the other end", 1500);
  }

  // ---- annotating and signing ------------------------------------------------------
  openAnnotate() {
    if (!this.doc) return;
    this.annotbar.hidden = false;
    this.annotateBtn.setAttribute("aria-pressed", "true");
    this.#showSaveState();
    this.#showEditMode(this.editMode || "NONE");
  }
  closeAnnotate() {
    if (this.annotbar.hidden) return;
    this.annotbar.hidden = true;
    this.annotateBtn.setAttribute("aria-pressed", "false");
    this.#showSaveState();
    if (this.editMode && this.editMode !== "NONE") this.#setEditMode("NONE");
  }

  #modeName(mode) {
    return Object.entries(pdfjs.AnnotationEditorType).find(([, v]) => v === mode)?.[0] || "NONE";
  }

  /** Switch the pdf.js editing tool: NONE (select), HIGHLIGHT, FREETEXT, INK
   *  or STAMP; or EDITTEXT, this reader's own tool for changing words. */
  #setEditMode(name, create = false) {
    if (!this.pdfViewer || !this.doc) return;
    const editText = name === "EDITTEXT";
    if (editText && !this.textEditHinted) {
      this.textEditHinted = true;
      this.toast("Click a line to change it, Shift+click for its whole paragraph, or click an empty spot to add words. Old words can stay in the file underneath — to remove text for good, use Redact.", 8000);
    }
    const markup = MARKUP.has(name), custom = editText || markup;
    if (name !== "NONE" || custom) this.measureTool(null);
    this.textEditMode = editText;
    this.markupMode = markup ? name : null;
    this.#cancelMarkup();
    this.root.classList.toggle("rd-edittext", editText);
    this.root.classList.toggle("rd-markup", markup);
    this.root.dataset.markup = markup ? name.toLowerCase() : "";
    this.pickedImage = null;
    this.#objectBoxes();
    if (custom) name = "NONE";
    const mode = pdfjs.AnnotationEditorType[name];
    if (mode === undefined) return;
    if (this.annotbar.hidden && (name !== "NONE" || custom)) this.openAnnotate();
    try {
      if (!(custom && this.pdfViewer.annotationEditorMode === mode)) this.pdfViewer.annotationEditorMode = { mode };
    } catch (e) {
      if (!custom) { this.toast("Annotating isn't available for this document."); return; }
    }
    this.#showEditMode(name);
    // Words selected before choosing Underline or Strike out get it straight away.
    if (this.markupMode === "UNDERLINE" || this.markupMode === "STRIKEOUT") this.#markSelection();
    if (this.markupMode === "REDACT") this.#redactSelection();
    // "Picture" asks for an image file straight away.
    if (create && this.uiManager) setTimeout(() => this.uiManager.updateParams(pdfjs.AnnotationEditorParamsType.CREATE, null), 50);
  }

  #showEditMode(name) {
    if (name === "NONE" && this.textEditMode) name = "EDITTEXT";
    if (name === "NONE" && this.markupMode) name = this.markupMode;
    this.editMode = name;
    if (name === "EDITTEXT" || MARKUP.has(name)) this.undoBtn.disabled = !this.activeTab?.undo?.length;
    for (const btn of this.annotTools) btn.setAttribute("aria-pressed", String(btn.dataset.mode === name));
    const sizes = name === "FREETEXT" ? [[10, "10 pt"], [12, "12 pt"], [14, "14 pt"], [18, "18 pt"], [24, "24 pt"], [36, "36 pt"]]
      : name === "INK" ? [[1, "Thin"], [2, "Fine"], [4, "Medium"], [8, "Thick"], [14, "Marker"]]
      : name === "HIGHLIGHT" ? [[8, "Thin"], [12, "Normal"], [20, "Wide"]]
      : name === "SHAPE" ? [[1, "Thin"], [2, "Medium"], [4, "Thick"], [8, "Heavy"]] : [];
    const key = { FREETEXT: "text", INK: "ink", HIGHLIGHT: "mark", SHAPE: "shape", NOTE: "note", UNDERLINE: "line", STRIKEOUT: "line" }[name];
    this.shapeSelect.hidden = name !== "SHAPE";
    this.stampSelect.hidden = this.stampDatedLabel.hidden = name !== "STAMPS";
    const chosen = this.annotParams?.size?.[key] ?? (key === "shape" ? 2 : undefined);
    this.sizeSelect.replaceChildren(...sizes.map(([v, label]) => el("option", { value: v, selected: v === chosen }, label)));
    this.sizeSelect.hidden = !sizes.length;
    this.root.querySelector(".rd-swatches").hidden = !key;
    this.#showRedactBar(name);
  }

  #applyAnnotParams() {
    this.annotParams ||= store.get("pdfm-annot", { color: 1, size: { text: 12, ink: 2, mark: 12 } });
    this.#setColor(this.annotParams.color, false);
    for (const [key, value] of Object.entries(this.annotParams.size)) this.#setSize(value, key, false);
  }

  #setColor(index, remember = true) {
    const [, ink, mark] = COLORS[index] || COLORS[1];
    for (const sw of this.swatches) sw.setAttribute("aria-pressed", String(+sw.dataset.index === index));
    const P = pdfjs.AnnotationEditorParamsType;
    if (this.uiManager) {
      // Applies to the selected annotations, or else to new ones.
      this.uiManager.updateParams(P.FREETEXT_COLOR, ink);
      this.uiManager.updateParams(P.INK_COLOR, ink);
      this.uiManager.updateParams(P.HIGHLIGHT_COLOR, mark);
    }
    if (remember) { this.annotParams.color = index; store.set("pdfm-annot", this.annotParams); }
  }

  #setSize(value, key = { FREETEXT: "text", INK: "ink", HIGHLIGHT: "mark", SHAPE: "shape" }[this.editMode], remember = true) {
    if (!key || !value) return;
    const P = pdfjs.AnnotationEditorParamsType;
    const type = { text: P.FREETEXT_SIZE, ink: P.INK_THICKNESS, mark: P.HIGHLIGHT_THICKNESS }[key];
    if (this.uiManager && type !== undefined) this.uiManager.updateParams(type, value);
    if (remember) { this.annotParams.size[key] = value; store.set("pdfm-annot", this.annotParams); }
  }

  // ---- notes, underline, strikeout, shapes and comments (markup.py) ---------------
  /** The colour picked in the annotation bar, as #rrggbb. */
  #markupColor() {
    const [, ink] = COLORS[this.annotParams?.color ?? 1] || COLORS[1];
    return ink;
  }

  /** Where a pointer event is on a page: { n, pt (PDF), view, box } or null. */
  #pointOnPage(e) {
    const pageEl = e.target.closest?.(".page") || document.elementFromPoint(e.clientX, e.clientY)?.closest?.(".page");
    const n = Number(pageEl?.dataset.pageNumber);
    const view = n ? this.pdfViewer.getPageView(n - 1) : null;
    if (!view?.div || !view.viewport) return null;
    const box = view.div.getBoundingClientRect();
    const x = e.clientX - box.left - view.div.clientLeft, y = e.clientY - box.top - view.div.clientTop;
    const pt = view.viewport.convertToPdfPoint(x, y);
    return Number.isFinite(pt[0]) && Number.isFinite(pt[1]) ? { n, pt, view, x, y } : null;
  }

  /** Send ``changes`` (see markup.py) to be written into the document. */
  #applyMarkup(changes, failure = "Could not add that: ") {
    return this.#changeDocument((bytes, tab) => this.#post("/api/markup", bytes, tab, {
      changes: JSON.stringify(changes), author: this.authorInput.value.trim() }), { failure });
  }

  #cancelMarkup() {
    this.shapeDraft?.el.remove();
    this.shapeDraft = null;
    this.noteBox?.remove();
    this.noteBox = null;
  }

  #markupDown(e) {
    const mode = this.markupMode;
    if (!mode || e.button !== 0 || mode === "UNDERLINE" || mode === "STRIKEOUT" || (mode === "REDACT" && this.#redactByText())) return;
    if (e.target.closest?.(".rd-notebox, .rd-objbox, .rd-objtools, a, input, textarea, select, button")) return;
    const at = this.#pointOnPage(e);
    if (mode === "IMAGES") { if (at) this.#pickImage(null); return; }  // a click beside the pictures lets go
    if (!at) return;
    e.preventDefault();
    e.stopPropagation();
    if (mode === "NOTE") { this.#openNoteBox(at); return; }
    if (mode === "LINKS") { this.#drawLinkBox(e, at); return; }
    if (mode === "CERTSIGN") { this.#dragBox(at, "Drag a box where the signature goes.", (rect) => this.#signWithId(at.n, rect)); return; }
    if (mode === "STAMPS") { this.#placeStamp(at); return; }
    if (mode === "REDACT") { this.#dragBox(at, "Drag a box over what should be blacked out.", (rect) => this.#addRedactions(at.n, [rect])); return; }
    // SHAPE: drag out the shape; it's drawn on screen until the file has it.
    this.#cancelMarkup();
    const svgEl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svgEl.setAttribute("class", "rd-shapedraft");
    at.view.div.append(svgEl);
    this.shapeDraft = { el: svgEl, start: at, end: at, kind: this.shapeSelect.value, pointer: e.pointerId };
    this.container.setPointerCapture?.(e.pointerId);
  }

  #markupMove(e) {
    const d = this.shapeDraft;
    if (!d || e.pointerId !== d.pointer) return;
    const view = d.start.view, box = view.div.getBoundingClientRect();
    let x = e.clientX - box.left - view.div.clientLeft, y = e.clientY - box.top - view.div.clientTop;
    const line = d.kind === "arrow" || d.kind === "line";
    if (e.shiftKey) {  // a square or circle; a line at 45° steps
      const dx = x - d.start.x, dy = y - d.start.y;
      if (line) {
        const a = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4), len = Math.hypot(dx, dy);
        x = d.start.x + len * Math.cos(a); y = d.start.y + len * Math.sin(a);
      } else {
        const side = Math.max(Math.abs(dx), Math.abs(dy));
        x = d.start.x + Math.sign(dx || 1) * side; y = d.start.y + Math.sign(dy || 1) * side;
      }
    }
    d.end = { x, y, pt: view.viewport.convertToPdfPoint(x, y) };
    const color = this.#markupColor(), width = Math.max(1, (this.annotParams?.size?.shape || 2) * view.viewport.scale);
    const [x0, y0, x1, y1] = [Math.min(d.start.x, x), Math.min(d.start.y, y), Math.max(d.start.x, x), Math.max(d.start.y, y)];
    const shape = d.kind === "ellipse" ? `<ellipse cx="${(x0 + x1) / 2}" cy="${(y0 + y1) / 2}" rx="${(x1 - x0) / 2}" ry="${(y1 - y0) / 2}"/>`
      : line ? `<line x1="${d.start.x}" y1="${d.start.y}" x2="${x}" y2="${y}"${d.kind === "arrow" ? ' marker-end="url(#rd-arrowhead)"' : ""}/>`
      : `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"${d.kind === "cloud" ? ' stroke-dasharray="2 3" rx="6"' : ""}/>`;
    d.el.innerHTML = `<defs><marker id="rd-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">`
      + `<path d="M0 0 10 5 0 10z" fill="${color}"/></marker></defs><g fill="none" stroke="${color}" stroke-width="${width}">${shape}</g>`;
  }

  #markupUp(e) {
    const mode = this.markupMode;
    if (!mode) return;
    if (mode === "UNDERLINE" || mode === "STRIKEOUT") {
      if (e.target.closest?.(".rd-container")) setTimeout(() => this.#markSelection(), 0);
      return;
    }
    if (mode === "REDACT") {
      if (this.#redactByText() && e.target.closest?.(".rd-container")) setTimeout(() => this.#redactSelection(), 0);
      return;
    }
    const d = this.shapeDraft;
    if (!d || e.pointerId !== d.pointer) return;
    this.#markupMove(e);
    this.shapeDraft = null;
    const { start, end, kind } = d;
    if (Math.hypot(end.x - start.x, end.y - start.y) < 4) { d.el.remove(); return; }  // a click, not a drag
    const page = start.n, color = this.#markupColor(), width = this.annotParams?.size?.shape || 2;
    const change = kind === "arrow" || kind === "line"
      ? { op: "add", page, kind, points: [start.pt, end.pt], color, width }
      : { op: "add", page, kind, rect: [start.pt[0], start.pt[1], end.pt[0], end.pt[1]], color, width };
    this.#applyMarkup([change]).finally(() => d.el.remove());
  }

  // -- stamps --
  /** A stamp where the page was clicked (the usual size), or in the box
   *  dragged out. */
  #placeStamp(at) {
    const view = at.view, draft = el("div", { class: "rd-linkdraft rd-stampdraft", style: `left:${at.x}px;top:${at.y}px;width:0;height:0` });
    view.div.append(draft);
    const box = view.div.getBoundingClientRect(), ox = box.left + view.div.clientLeft, oy = box.top + view.div.clientTop;
    let end = [at.x, at.y];
    const move = (ev) => {
      end = [ev.clientX - ox, ev.clientY - oy];
      Object.assign(draft.style, { left: Math.min(at.x, end[0]) + "px", top: Math.min(at.y, end[1]) + "px",
        width: Math.abs(end[0] - at.x) + "px", height: Math.abs(end[1] - at.y) + "px" });
    };
    const up = async () => {
      window.removeEventListener("pointermove", move);
      draft.remove();
      const dated = this.stampDated.checked, s = view.viewport.scale;
      let a = [at.x, at.y], b = end;
      if (Math.abs(end[0] - at.x) < 12 || Math.abs(end[1] - at.y) < 8) {  // a click: the usual size, centred there
        const w = 160 * s, h = (dated ? 50 : 42) * s;
        a = [at.x - w / 2, at.y - h / 2]; b = [at.x + w / 2, at.y + h / 2];
      }
      let label = this.stampSelect.value;
      if (!label) {
        const words = el("input", { type: "text", value: store.get("pdfm-stamp-own", ""), placeholder: "e.g. PAID", "aria-label": "Stamp words", maxlength: "60" });
        setTimeout(() => words.focus(), 0);
        if (await this.#dialog("Your own stamp", [el("label", { class: "rd-field" }, "Words on the stamp", words)],
          [["cancel", "Cancel"], ["ok", "Stamp", true]]) !== "ok" || !words.value.trim()) return;
        label = words.value.trim();
        store.set("pdfm-stamp-own", label);
      }
      store.set("pdfm-stamp", this.stampSelect.value);
      store.set("pdfm-stamp-dated", dated);
      const p = view.viewport.convertToPdfPoint(...a), q = view.viewport.convertToPdfPoint(...b);
      const rect = [Math.min(p[0], q[0]), Math.min(p[1], q[1]), Math.max(p[0], q[0]), Math.max(p[1], q[1])];
      this.#applyMarkup([{ op: "add", page: at.n, kind: "stamp", rect, label, dated }], "Could not stamp the page: ");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  }
  // -- end of stamps --

  /** Underline or strike out the selected words. */
  #markSelection() {
    const kind = { UNDERLINE: "underline", STRIKEOUT: "strikeout" }[this.markupMode];
    const sel = window.getSelection();
    if (!kind || !sel || sel.isCollapsed || !sel.rangeCount) return;
    const pages = new Map();
    for (const r of sel.getRangeAt(0).getClientRects()) {
      if (r.width < 1 || r.height < 1) continue;
      const at = this.#pointOnPage({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, target: document.body });
      if (!at || !this.container.contains(at.view.div)) continue;
      const box = at.view.div.getBoundingClientRect(), dx = box.left + at.view.div.clientLeft, dy = box.top + at.view.div.clientTop;
      const a = at.view.viewport.convertToPdfPoint(r.left - dx, r.top - dy), b = at.view.viewport.convertToPdfPoint(r.right - dx, r.bottom - dy);
      const rect = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
      if (!pages.has(at.n)) pages.set(at.n, []);
      pages.get(at.n).push(rect);
    }
    if (!pages.size) return;
    sel.removeAllRanges();
    const color = this.#markupColor();
    this.#applyMarkup([...pages].map(([page, rects]) => ({ op: "add", page, kind, rects, color })));
  }

  /** A box to type a note into, where the page was clicked. */
  #openNoteBox(at) {
    this.#cancelMarkup();
    const text = el("textarea", { rows: 4, placeholder: "Your comment", "aria-label": "Note" });
    const add = el("button", { type: "button", class: "rd-btn primary" }, "Add note");
    const cancel = el("button", { type: "button", class: "rd-btn" }, "Cancel");
    const box = el("div", { class: "rd-notebox", style: `left:${at.x}px;top:${at.y}px` }, text, el("div", { class: "rd-notebtns" }, cancel, add));
    at.view.div.append(box);
    this.noteBox = box;
    const done = () => {
      const words = text.value.trim();
      this.#cancelMarkup();
      if (words) this.#applyMarkup([{ op: "add", page: at.n, kind: "note", at: at.pt, text: words, color: "#f5c400" }]);
    };
    add.addEventListener("click", done);
    cancel.addEventListener("click", () => this.#cancelMarkup());
    text.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") this.#cancelMarkup();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) done();
    });
    setTimeout(() => text.focus(), 0);
  }

  // ---- the Comments tab --------------------------------------------------------------
  /** Every annotation with what's written on it, by page, replies under what they answer. */
  async #listComments() {
    const doc = this.doc, list = this.commentsList;
    if (!doc || this.commentsPanel.hidden) return;
    const seq = (this.commentsSeq = (this.commentsSeq || 0) + 1);
    list.replaceChildren(el("p", { class: "rd-muted" }, "Reading comments…"));
    const items = [];
    for (let n = 1; n <= doc.numPages; n++) {
      let annots = [];
      try { annots = await (await doc.getPage(n)).getAnnotations({ intent: "display" }); } catch (e) { /* skip the page */ }
      if (seq !== this.commentsSeq || doc !== this.doc) return;
      for (const a of annots) {
        if (!KIND_NAMES[a.subtype] || a.replyType === "Group") continue;
        items.push({ page: n, id: a.id, kind: a.subtype === "Line" && a.lineEndings?.[1] && a.lineEndings[1] !== "None" ? "Arrow" : KIND_NAMES[a.subtype],
          text: a.contentsObj?.str || "", author: a.titleObj?.str || "", date: a.modificationDate || a.creationDate,
          reply: a.inReplyTo || null, rect: a.rect, color: a.color });
      }
    }
    const byId = new Map(items.map((it) => [it.id, it]));
    const top = items.filter((it) => !it.reply || !byId.has(it.reply));
    for (const it of items) if (it.reply && byId.has(it.reply)) (byId.get(it.reply).replies ||= []).push(it);
    const fresh = this.#changed() ? [el("p", { class: "rd-muted" }, "Highlights and drawings not saved yet show here once you save or add a note.")] : [];
    if (!top.length) {
      list.replaceChildren(...fresh, el("p", { class: "rd-muted" }, "No comments or markup yet. Use Note, Underline, Strike out or Shapes in Annotate & sign."));
      return;
    }
    list.replaceChildren(...fresh, ...top.map((it) => this.#commentCard(it)));
  }

  // -- comment summary and XFDF --
  async #commentRequest(what, extra = null) {
    await this.chain;
    const tab = this.activeTab;
    const body = new FormData();
    body.append("file", new Blob([await this.#currentBytes()], { type: "application/pdf" }), "file.pdf");
    body.append("password", tab?.password || "");
    body.append("name", this.name || "document.pdf");
    if (extra) body.append("xfdf", extra, extra.name);
    const res = await fetch(`/api/comments/${what}`, { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return res;
  }

  /** A PDF listing the comments, opened in a new tab. */
  async #commentSummary() {
    if (!this.doc) return;
    try {
      const res = await this.#commentRequest("summary");
      const stem = (this.name || "document").replace(/\.pdf$/i, "");
      await this.openBytes(new Uint8Array(await res.arrayBuffer()), `${stem} — comments.pdf`);
    } catch (e) { this.toast("Couldn't make the summary: " + (e.message || e)); }
  }

  async #exportComments() {
    if (!this.doc) return;
    try {
      const res = await this.#commentRequest("export");
      const n = Number(res.headers.get("X-Pdfm-Count")) || 0;
      if (!n) { this.toast("This document has no comments to export."); return; }
      const stem = (this.name || "document").replace(/\.pdf$/i, "");
      await this.#saveBytes(new Uint8Array(await res.arrayBuffer()), `${stem}.xfdf`, () => {}, this.activeTab?.fileId);
    } catch (e) { this.toast("Couldn't export the comments: " + (e.message || e)); }
  }

  #importComments(file) {
    return this.#changeDocument(async (bytes, tab) => {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      body.append("password", tab.password || "");
      body.append("xfdf", file, file.name);
      return fetch("/api/comments/import", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    }, {
      failure: "Couldn't add the comments: ",
      after: (res) => {
        const n = Number(res.headers.get("X-Pdfm-Added")) || 0, skipped = res.headers.get("X-Pdfm-Skipped");
        this.toast(n ? `Added ${n} comment${n === 1 ? "" : "s"}${skipped ? ` (some kinds couldn't be added: ${skipped})` : ""}.`
          : "Nothing to add: these comments are already here.");
        this.#listComments();
      },
    });
  }
  // -- end of comment summary and XFDF --

  #commentCard(it, isReply = false) {
    const date = (raw) => {
      const d = raw && pdfjs.PDFDateString.toDateObject(raw);
      return d ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
    };
    const swatch = it.color ? `rgb(${[...it.color].join(",")})` : "transparent";
    const head = el("div", { class: "rd-commenthead" },
      el("i", { class: "rd-commentdot", style: `background:${swatch}` }),
      el("b", {}, isReply ? "Reply" : it.kind), el("span", {}, it.author ? " · " + it.author : ""),
      el("span", { class: "rd-muted" }, ` · p. ${this.labels?.[it.page - 1] || it.page}`));
    const body = el("p", { class: "rd-commenttext" + (it.text ? "" : " rd-muted") }, it.text || "No comment");
    const when = el("div", { class: "rd-muted rd-commentdate" }, date(it.date));
    const btn = (label, fn) => { const b = el("button", { type: "button", class: "rd-linkbtn" }, label); b.addEventListener("click", (e) => { e.stopPropagation(); fn(); }); return b; };
    const card = el("article", { class: "rd-comment" + (isReply ? " reply" : ""), tabindex: "0" }, head, body, when);
    const ask = (label, value, apply) => {
      card.querySelector(".rd-commentedit")?.remove();
      const area = el("textarea", { rows: 3, "aria-label": label }, value);
      const ok = el("button", { type: "button", class: "rd-btn primary" }, label);
      const box = el("div", { class: "rd-commentedit" }, area, el("div", { class: "rd-notebtns" }, btn("Cancel", () => box.remove()), ok));
      ok.addEventListener("click", (e) => { e.stopPropagation(); apply(area.value.trim()); });
      area.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ok.click(); if (e.key === "Escape") box.remove(); });
      card.append(box);
      area.focus();
    };
    card.append(el("div", { class: "rd-commentbtns" },
      isReply ? null : btn("Reply", () => ask("Reply", "", (text) => text && this.#applyMarkup([{ op: "reply", page: it.page, id: it.id, text }], "Could not reply: "))),
      btn(it.text ? "Edit" : "Add comment", () => ask("Save", it.text, (text) => this.#applyMarkup([{ op: "text", page: it.page, id: it.id, text }], "Could not change the comment: "))),
      btn("Delete", () => {
        if (confirm(isReply ? "Delete this reply?" : `Delete this ${it.kind.toLowerCase()}${it.replies?.length ? " and its replies" : ""}?`)) {
          this.#applyMarkup([{ op: "delete", page: it.page, id: it.id }], "Could not delete it: ");
        }
      })));
    card.addEventListener("click", () => this.#showComment(it));
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === card) this.#showComment(it); });
    for (const r of it.replies || []) card.append(this.#commentCard(r, true));
    return card;
  }

  /** Scroll to an annotation and flash it. */
  #showComment(it) {
    const [x0, y0, x1, y1] = it.rect || [0, 0, 0, 0];
    this.pdfViewer.scrollPageIntoView({ pageNumber: it.page, destArray: [null, { name: "XYZ" }, x0 - 40, y1 + 60, null] });
    setTimeout(() => {
      const view = this.pdfViewer.getPageView(it.page - 1);
      if (!view?.div || !view.viewport) return;
      const [a, b, c, d] = viewRect(view.viewport, [x0, y0, x1, y1]);
      const flash = el("div", { class: "rd-flash", style: `left:${Math.min(a, c) - 4}px;top:${Math.min(b, d) - 4}px;width:${Math.abs(c - a) + 8}px;height:${Math.abs(d - b) + 8}px` });
      view.div.append(flash);
      setTimeout(() => flash.remove(), 1600);
    }, 150);
  }

  // ---- pictures and links on the page (pageimages.py, markup.py) -------------------
  /** Pictures or Links mode: outlines over the pages' pictures or links. */
  #objectBoxes() {
    this.viewerEl?.querySelectorAll(".rd-objbox, .rd-objtools, .rd-linkdraft").forEach((n) => n.remove());
    if (this.markupMode === "CERTSIGN") this.toast("Drag a box where your signature should appear, then choose your digital ID.", 5000);
    if ((this.markupMode !== "IMAGES" && this.markupMode !== "LINKS") || !this.doc) return;
    for (let i = 0; i < this.pdfViewer.pagesCount; i++) {
      if (this.pdfViewer.getPageView(i)?.canvas?.width) this.#drawObjectBoxes(i + 1);
    }
    if (this.markupMode === "IMAGES" && !this.imagesHinted) {
      this.imagesHinted = true;
      this.toast("Click a picture to pick it: drag it to move it, drag a corner to resize it (Shift: freely), or replace or delete it.", 6000);
    }
  }

  /** The pictures of the shown document, by page (asked once per change of it). */
  #imagesOf(tab) {
    if (tab.imagesFor !== (tab.version || 0) || !tab.images) {
      tab.imagesFor = tab.version || 0;
      tab.images = (async () => {
        const body = new FormData();
        body.append("file", new Blob([await this.#fullBytes()], { type: "application/pdf" }), "file.pdf");
        body.append("password", tab.password || "");
        const res = await fetch("/api/images/list", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
        return res.ok ? (await res.json()).pages || {} : {};
      })().catch(() => ({}));
    }
    return tab.images;
  }

  /** Where a link goes: { url } or { page }. */
  async #linkTarget(a) {
    if (a.url || a.unsafeUrl) return { url: a.url || a.unsafeUrl };
    try {
      let dest = a.dest;
      if (typeof dest === "string") dest = await this.doc.getDestination(dest);
      if (Array.isArray(dest) && dest[0]) {
        const index = typeof dest[0] === "object" ? await this.doc.getPageIndex(dest[0]) : dest[0];
        return { page: index + 1 };
      }
    } catch (e) { /* somewhere this reader can't name */ }
    return {};
  }

  async #drawObjectBoxes(n) {
    const view = this.pdfViewer.getPageView(n - 1), tab = this.activeTab, mode = this.markupMode, doc = this.doc;
    if (!view?.div || !view.viewport || !tab) return;
    let items = [];
    if (mode === "IMAGES") items = (await this.#imagesOf(tab))[n] || [];
    else if (mode === "LINKS") {
      try { items = (await (await doc.getPage(n)).getAnnotations({ intent: "display" })).filter((a) => a.subtype === "Link"); } catch (e) { items = []; }
    }
    if (mode !== this.markupMode || doc !== this.doc) return;
    view.div.querySelectorAll(".rd-objbox, .rd-objtools").forEach((x) => x.remove());
    const vp = view.viewport;
    for (const item of items) {
      const [a, b, c, d] = viewRect(vp, item.box || item.rect);
      const box = el("div", { class: "rd-objbox" + (mode === "LINKS" ? " link" : "") + (item.editable === false ? " locked" : ""),
        style: `left:${Math.min(a, c)}px;top:${Math.min(b, d)}px;width:${Math.abs(c - a)}px;height:${Math.abs(d - b)}px` });
      if (mode === "IMAGES") {
        box.title = item.editable === false ? "This picture is drawn as part of a group, so it can't be edited here" : "Drag to move; click to pick it";
        if (item.editable !== false) for (const h of ["nw", "ne", "sw", "se"]) box.append(el("i", { class: "rd-handle " + h, "data-handle": h }));
        box.addEventListener("pointerdown", (e) => this.#grabImage(e, n, item, box));
      } else {
        const target = await this.#linkTarget(item);
        box.title = target.url ? `Goes to ${target.url}` : target.page ? `Goes to page ${target.page}` : "A link";
        box.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); });
        box.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); this.#editLink(n, item, null, target); });
      }
      view.div.append(box);
      if (mode === "IMAGES" && this.pickedImage?.page === n && this.pickedImage.index === item.index) this.#pickImage({ page: n, item, box }, false);
    }
  }

  /** Pick a picture (null: none): its handles and Replace / Delete. */
  #pickImage(picked, redraw = true) {
    this.viewerEl.querySelectorAll(".rd-objbox.picked").forEach((b) => b.classList.remove("picked"));
    this.viewerEl.querySelectorAll(".rd-objtools").forEach((t) => t.remove());
    this.pickedImage = picked ? { page: picked.page, index: picked.item.index } : null;
    if (!picked) return;
    picked.box.classList.add("picked");
    const btn = (icon, label, fn) => {
      const b = el("button", { type: "button", class: "rd-btn", html: svg(icon) + `<span>${label}</span>` });
      b.addEventListener("pointerdown", (e) => e.stopPropagation());
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    };
    const tools = el("div", { class: "rd-objtools", style: `left:${picked.box.offsetLeft}px;top:${Math.max(0, picked.box.offsetTop - 42)}px` },
      btn("image", "Replace…", () => this.#editImage("replace")), btn("trash", "Delete", () => this.#editImage("delete")));
    picked.box.parentElement.append(tools);
    if (redraw) picked.box.focus?.();
  }

  /** Drag a picture (or one of its corners) to move (or resize) it. */
  #grabImage(e, page, item, box) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    if (item.editable === false) { this.toast("That picture is drawn as part of a group, so it can't be edited here.", 4000); return; }
    this.#pickImage({ page, item, box }, false);
    const handle = e.target.dataset?.handle || null;
    const start = { x: e.clientX, y: e.clientY, left: box.offsetLeft, top: box.offsetTop, width: box.offsetWidth, height: box.offsetHeight };
    const ratio = start.width / Math.max(1, start.height);
    let moved = false;
    const move = (ev) => {
      const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
      if (!moved && Math.hypot(dx, dy) < 3) return;
      moved = true;
      let { left, top, width, height } = start;
      if (!handle) { left += dx; top += dy; }
      else {
        if (handle.includes("e")) width = start.width + dx;
        if (handle.includes("w")) width = start.width - dx;
        if (handle.includes("s")) height = start.height + dy;
        if (handle.includes("n")) height = start.height - dy;
        width = Math.max(8, width); height = Math.max(8, height);
        if (!ev.shiftKey) {  // the picture keeps its proportions
          if (Math.abs(dx) * start.height >= Math.abs(dy) * start.width) height = width / ratio; else width = height * ratio;
        }
        if (handle.includes("w")) left = start.left + start.width - width;
        if (handle.includes("n")) top = start.top + start.height - height;
      }
      Object.assign(box.style, { left: left + "px", top: top + "px", width: width + "px", height: height + "px" });
      this.viewerEl.querySelectorAll(".rd-objtools").forEach((t) => { t.style.left = left + "px"; t.style.top = Math.max(0, top - 42) + "px"; });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      if (!moved) return;
      const view = this.pdfViewer.getPageView(page - 1), vp = view.viewport;
      const [x0, y0] = vp.convertToPdfPoint(box.offsetLeft, box.offsetTop);
      const [x1, y1] = vp.convertToPdfPoint(box.offsetLeft + box.offsetWidth, box.offsetTop + box.offsetHeight);
      this.#changeImages([{ op: "move", page, index: item.index, box: [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)] }]);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  }

  async #editImage(op) {
    const picked = this.pickedImage;
    if (!picked) return;
    if (op === "delete") { this.pickedImage = null; await this.#changeImages([{ op: "delete", page: picked.page, index: picked.index }]); return; }
    const file = await new Promise((resolve) => {
      const input = el("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp,image/bmp,image/tiff", hidden: true });
      input.addEventListener("change", () => { resolve(input.files[0] || null); input.remove(); });
      input.addEventListener("cancel", () => { resolve(null); input.remove(); });
      this.root.append(input);
      input.click();
    });
    if (file) await this.#changeImages([{ op: "replace", page: picked.page, index: picked.index, picture: "picture0" }], [file]);
  }

  #changeImages(changes, files = []) {
    return this.#changeDocument((bytes, tab) => {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      body.append("password", tab.password || "");
      body.append("changes", JSON.stringify(changes));
      files.forEach((f, i) => body.append("picture" + i, f, f.name || "picture"));
      return fetch("/api/images/edit", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    }, { failure: "Could not change the picture: " });
  }

  /** Links mode: drag a box on the page for a new link. */
  #drawLinkBox(e, at) {
    this.#dragBox(at, "Drag a box over what should be clickable to add a link.", (rect) => this.#editLink(at.n, null, rect));
  }

  /** Drag out a box on the page from ``at``; ``done(rect)`` gets it in PDF coordinates. */
  #dragBox(at, tooSmall, done) {
    const view = at.view, draft = el("div", { class: "rd-linkdraft", style: `left:${at.x}px;top:${at.y}px;width:0;height:0` });
    view.div.append(draft);
    const box = view.div.getBoundingClientRect(), ox = box.left + view.div.clientLeft, oy = box.top + view.div.clientTop;
    let end = [at.x, at.y];
    const move = (ev) => {
      end = [ev.clientX - ox, ev.clientY - oy];
      Object.assign(draft.style, { left: Math.min(at.x, end[0]) + "px", top: Math.min(at.y, end[1]) + "px",
        width: Math.abs(end[0] - at.x) + "px", height: Math.abs(end[1] - at.y) + "px" });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      draft.remove();
      if (Math.abs(end[0] - at.x) < 6 || Math.abs(end[1] - at.y) < 6) { this.toast(tooSmall, 3000); return; }
      const a = view.viewport.convertToPdfPoint(at.x, at.y), b = view.viewport.convertToPdfPoint(...end);
      done([Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  }

  /** Change where a link goes, or delete it; or say where a new one goes. */
  async #editLink(n, link, rect, target = {}) {
    const toWeb = el("input", { type: "radio", name: "rd-linkto", value: "web" });
    const toPage = el("input", { type: "radio", name: "rd-linkto", value: "page" });
    const url = el("input", { type: "text", value: target.url || "", placeholder: "https://example.com or name@example.com", "aria-label": "Web or email address" });
    const page = el("input", { type: "number", min: "1", max: String(this.doc.numPages), value: target.page ? String(target.page) : "", "aria-label": "Page" });
    (target.page && !target.url ? toPage : toWeb).checked = true;
    url.addEventListener("input", () => { toWeb.checked = true; });
    page.addEventListener("input", () => { toPage.checked = true; });
    setTimeout(() => (toPage.checked ? page : url).focus(), 0);
    const answer = await this.#dialog(link ? "Link" : "New link", [
      el("label", { class: "rd-field" }, el("span", {}, toWeb, " Goes to a web or email address"), url),
      el("label", { class: "rd-field" }, el("span", {}, toPage, ` Goes to a page of this document (1–${this.doc.numPages})`), page)],
      link ? [["delete", "Delete the link"], ["cancel", "Cancel"], ["ok", "Save", true]] : [["cancel", "Cancel"], ["ok", "Add the link", true]]);
    if (answer === "delete") { await this.#applyMarkup([{ op: "delete", page: n, id: link.id }], "Could not delete the link: "); return; }
    if (answer !== "ok") return;
    const where = toPage.checked ? { to_page: Number(page.value) } : { url: url.value.trim() };
    if (!where.url && !where.to_page) { this.toast("Say where the link goes."); return; }
    if (link) await this.#applyMarkup([{ op: "link", page: n, id: link.id, ...where }], "Could not change the link: ");
    else await this.#applyMarkup([{ op: "add", page: n, kind: "link", rect, ...where }], "Could not add the link: ");
  }

  // -- reader redaction --------------------------------------------------------------------
  // Mark areas to black out (drag boxes, or select words), then Apply sends
  // them to /api/redact/apply (redact.py, method "remove"): what's under them
  // is taken out of the file and black boxes are drawn. The marks belong to
  // the tab and to the version of the document they were made on.

  #buildRedact() {
    this.redactBtn = el("button", { class: "rd-btn rd-tool", type: "button", "data-mode": "REDACT", "aria-pressed": "false",
      title: "Redact: mark what should be blacked out for good, then Apply", html: svg("redact") + "<span>Redact</span>" });
    this.annotTools.push(this.redactBtn);
    this.redactHow = el("select", { class: "rd-zoom", "aria-label": "How to mark", hidden: true },
      el("option", { value: "box" }, "Draw boxes"), el("option", { value: "text" }, "Select words"));
    this.redactHow.value = store.get("pdfm-redact-how", "box");
    this.redactHow.addEventListener("change", () => {
      store.set("pdfm-redact-how", this.redactHow.value);
      this.root.dataset.redact = this.redactHow.value;
    });
    this.redactApplyBtn = el("button", { class: "rd-btn primary", type: "button", hidden: true, disabled: true,
      title: "Black out the marked areas for good", html: svg("redact") + "<span>Apply</span>" });
    this.redactApplyBtn.addEventListener("click", () => this.#applyRedactions());
  }

  #redactByText() { return this.redactHow?.value === "text"; }

  /** The tab's marks, {page: [[x0, y0, x1, y1]]} in PDF points; marks made on
   *  an earlier version of the document are dropped (its pages may have moved). */
  #redactMarks(tab = this.activeTab) {
    if (!tab) return {};
    const version = tab.version || 0;
    if (tab.redactMarks?.version !== version) tab.redactMarks = { version, pages: {} };
    return tab.redactMarks.pages;
  }

  #redactCount(tab = this.activeTab) {
    return Object.values(this.#redactMarks(tab)).reduce((n, list) => n + list.length, 0);
  }

  #showRedactBar(name) {
    const on = name === "REDACT";
    if (!this.redactHow) return;
    this.redactHow.hidden = this.redactApplyBtn.hidden = !on;
    this.root.dataset.redact = on ? this.redactHow.value : "";
    if (on) this.#showRedactCount();
    if (on && !this.redactHinted) {
      this.redactHinted = true;
      this.toast("Drag boxes over what should go (or choose Select words), then Apply redactions. Click × on a mark to remove it.", 7000);
    }
  }

  #showRedactCount() {
    const n = this.#redactCount();
    this.redactApplyBtn.disabled = !n;
    this.redactApplyBtn.querySelector("span").textContent = n ? `Apply (${n})` : "Apply";
    this.redactApplyBtn.title = n ? `Black out the ${n === 1 ? "marked area" : n + " marked areas"} for good` : "Mark something to black out first";
  }

  #addRedactions(n, rects) {
    const list = (this.#redactMarks()[n] ||= []);
    for (const r of rects) list.push(r.map((v) => Math.round(v * 100) / 100));
    this.#drawRedactions(n);
    this.#showRedactCount();
  }

  /** Red outlines (with a remove button) over the marks on page ``n``. */
  #drawRedactions(n) {
    const view = this.pdfViewer?.getPageView(n - 1);
    if (!view?.div || !view.viewport) return;
    view.div.querySelectorAll(".rd-redactmark").forEach((x) => x.remove());
    const list = this.#redactMarks()[n] || [];
    list.forEach((rect, i) => {
      const [a, b, c, d] = viewRect(view.viewport, rect);
      const remove = el("button", { type: "button", class: "rd-redactx", title: "Remove this mark", "aria-label": "Remove this mark", html: svg("x") });
      remove.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); });
      remove.addEventListener("click", (e) => {
        e.stopPropagation();
        list.splice(i, 1);
        this.#drawRedactions(n);
        this.#showRedactCount();
      });
      view.div.append(el("div", { class: "rd-redactmark", title: "Marked for redaction",
        style: `left:${Math.min(a, c)}px;top:${Math.min(b, d)}px;width:${Math.abs(c - a)}px;height:${Math.abs(d - b)}px` }, remove));
    });
  }

  /** Mark the selected words (one box per line piece, a little padded). */
  #redactSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const pages = new Map();
    for (const r of sel.getRangeAt(0).getClientRects()) {
      if (r.width < 1 || r.height < 1) continue;
      const at = this.#pointOnPage({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, target: document.body });
      if (!at || !this.container.contains(at.view.div)) continue;
      const box = at.view.div.getBoundingClientRect(), dx = box.left + at.view.div.clientLeft, dy = box.top + at.view.div.clientTop;
      const a = at.view.viewport.convertToPdfPoint(r.left - dx, r.top - dy), b = at.view.viewport.convertToPdfPoint(r.right - dx, r.bottom - dy);
      const pad = 0.75;
      const rect = [Math.min(a[0], b[0]) - pad, Math.min(a[1], b[1]) - pad, Math.max(a[0], b[0]) + pad, Math.max(a[1], b[1]) + pad];
      if (!pages.has(at.n)) pages.set(at.n, []);
      pages.get(at.n).push(rect);
    }
    if (!pages.size) return;
    sel.removeAllRanges();
    for (const [n, rects] of pages) this.#addRedactions(n, rects);
  }

  async #applyRedactions() {
    const tab = this.activeTab, marks = this.#redactMarks(tab), count = this.#redactCount(tab);
    if (!tab || !count) { this.toast("Mark something first: drag a box over it, or choose Select words."); return; }
    const answer = await this.#dialog("Apply redactions?", [
      el("p", {}, `This removes what's under the ${count === 1 ? "marked area" : count + " marked areas"} for good once you save: `
        + "the words, the covered parts of pictures, and comments or form fields there. The rest of the page stays as it is."),
      el("p", {}, "Until then, Undo (Ctrl+Z) brings it back.")],
      [["cancel", "Cancel"], ["ok", "Apply redactions", true]]);
    if (answer !== "ok") return;
    const boxes = Object.fromEntries(Object.entries(marks).filter(([, list]) => list.length));
    await this.#changeDocument((bytes, t) => this.#post("/api/redact/apply", bytes, t, { boxes: JSON.stringify(boxes) }), {
      failure: "Could not redact: ",
      after: (res) => {
        let done = {};
        try { done = JSON.parse(res.headers.get("X-Pdfm-Redacted") || "{}"); } catch (e) { /* the default words do */ }
        const pictures = Object.entries(done.pictures || {});
        let message = `Blacked out ${done.areas || count} area${(done.areas || count) === 1 ? "" : "s"}.`;
        if (pictures.length) {
          message += ` Page${pictures.length === 1 ? "" : "s"} ${pictures.map(([p]) => p).join(", ")} became `
            + `${pictures.length === 1 ? "a picture" : "pictures"} (${[...new Set(pictures.map(([, why]) => why))].join("; ")}).`;
        }
        this.toast(message + " Save to make it permanent.", 8000, ["Save", () => this.save()]);
      },
    });
    if (this.markupMode === "REDACT") this.#showRedactCount();
  }
  // -- end of reader redaction --

  // ---- digital ID signatures (certsign.py) --------------------------------------------
  /** Sign the document with a digital ID, its signature shown in ``rect`` on page ``n``. */
  async #signWithId(n, rect) {
    const pfx = el("input", { type: "file", accept: ".pfx,.p12,application/x-pkcs12", "aria-label": "Digital ID file" });
    const password = el("input", { type: "password", autocomplete: "off", "aria-label": "Digital ID password" });
    const reason = el("input", { type: "text", placeholder: "e.g. I approve this document", "aria-label": "Reason" });
    const location = el("input", { type: "text", placeholder: "e.g. Vancouver", "aria-label": "Location" });
    const stamp = el("input", { type: "text", placeholder: "none (e.g. http://timestamp.digicert.com)", "aria-label": "Timestamp server",
      value: store.get("pdfm-timestamp-url", "") });
    const answer = await this.#dialog("Sign with a digital ID", [
      el("p", {}, "Your digital ID is a .pfx or .p12 file with your certificate. The signature seals the document: a change after signing shows up when it's checked."),
      el("label", { class: "rd-field" }, "Digital ID file", pfx), el("label", { class: "rd-field" }, "Its password", password),
      el("label", { class: "rd-field" }, "Reason (optional)", reason), el("label", { class: "rd-field" }, "Location (optional)", location),
      el("label", { class: "rd-field" }, "Timestamp server (optional)", stamp)],
      [["cancel", "Cancel"], ["ok", "Sign", true]]);
    if (answer !== "ok") return;
    const file = pfx.files[0];
    if (!file) { this.toast("Choose your digital ID file (.pfx or .p12)."); return; }
    store.set("pdfm-timestamp-url", stamp.value.trim());
    const tab = this.activeTab, version = tab?.version || 0;
    await this.#changeDocument((bytes, t) => {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      body.append("pfx", file, file.name);
      for (const [k, v] of Object.entries({ pfx_password: password.value, password: t.password || "", page: String(n), box: JSON.stringify(rect),
        reason: reason.value.trim(), location: location.value.trim(), timestamp_url: stamp.value.trim() })) body.append(k, v);
      return fetch("/api/sign/cert", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    }, { failure: "Could not sign: ", signing: true });
    if ((tab?.version || 0) !== version) {
      if (this.markupMode === "CERTSIGN") this.#setEditMode("NONE");
      tab.signedWarned = false;
      this.toast("Signed. Save it now: anything changed after this shows as changed after signing.", 7000, ["Save", () => this.save()]);
    }
  }

  /** What the document's signatures say: { signatures: [...] } (see certsign.verify). */
  async #verifySignatures() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return [];
    const body = new FormData();
    body.append("file", new Blob([await this.#fullBytes()], { type: "application/pdf" }), "file.pdf");
    body.append("password", tab.password || "");
    const res = await fetch("/api/sign/verify", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return (await res.json()).signatures || [];
  }

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
      if (tab.formHint !== "checking" || !tab.bytes || tab.bytes.length > 30e6) { tab.formHint = "none"; return; }
      const res = await this.#post("/api/form/detect", tab.bytes, tab, { pages: "1-2" });
      if (!res.ok) { tab.formHint = "none"; return; }
      const found = Object.values((await res.json()).fields || {}).flat();
      tab.formHint = found.length >= 3 ? "show" : "none";
      if (tab.formHint === "show" && doc === this.doc && tab === this.activeTab) this.#showFormHint(tab);
    } catch (e) {
      tab.formHint = "none";
    }
  }

  #showFormHint(tab) {
    this.formText.textContent = "This looks like a form to fill in, but you can't type in it yet.";
    this.formbar.hidden = false;
  }

  /** Add the fields of a printed form, so it can be filled in here. */
  makeFillable() {
    if (!this.doc) return;
    this.formbar.hidden = true;
    return this.#changeDocument((bytes, tab) => this.#post("/api/form/fillable", bytes, tab), {
      failure: "Couldn't make it fillable: ",
      after: (res) => {
        const n = Number(res.headers.get("X-Pdfm-Fields")) || 0;
        if (this.activeTab) this.activeTab.formHint = "has-fields";
        this.toast(`Added ${n} place${n === 1 ? "" : "s"} to fill in: click one to type or tick, then save. Add form fields (All tools) can fine-tune them.`);
      },
    });
  }

  async #checkSignatures() {
    const doc = this.doc, tab = this.activeTab;
    this.sigbar.hidden = true;
    if (!doc) return;
    // Every signature has a /ByteRange (pdf.js leaves signature fields out of
    // its list of fields). A big file read in pieces isn't searched.
    const bytes = tab?.bytes;
    if (!bytes || doc !== this.doc) return;
    if (!new TextDecoder("latin1").decode(bytes).includes("/ByteRange")) return;
    try {
      const list = await this.#verifySignatures();
      if (doc !== this.doc || tab !== this.activeTab || !list.length) return;
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
    await this.#dialog("Signatures", [el("ul", { class: "rd-siglist" }, ...items)], [["ok", "Close", true]]);
  }

  // ---- comparing revisions (overlay.js) -------------------------------------------
  /** Draw another version of this document over it: what changed shows in red
   *  (only in that version) and blue (only in this one). */
  async compareRevision() {
    if (!this.doc) return;
    const others = this.tabs.filter((t) => t !== this.activeTab);
    const answer = await this.#dialog("Compare with another revision", [el("p", {},
      "The other version is drawn over this one, page by page: what both have is black, what's only in that version red, "
      + "and what's only in this one blue. Pick the other version:")],
      [...others.map((t) => ["tab:" + t.id, t.name]), ["file", "Choose a file…"], ["cancel", "Cancel"]]);
    let source = null, name = "", password = null;
    if (answer.startsWith("tab:")) {
      const tab = others.find((t) => "tab:" + t.id === answer);
      if (!tab) return;
      source = tab.bytes ? { data: tab.bytes.slice() } : { url: tab.url, httpHeaders: { "X-Pdfm-Token": this.token }, length: tab.size || undefined,
        rangeChunkSize: 1 << 20, disableAutoFetch: true, disableStream: true };
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

  /** Draw, type or pick a signature, then place it on the current page. */
  async sign() {
    if (!this.doc || !this.uiManager) return;
    const image = await this.#signatureDialog();
    if (!image) return;
    const file = new File([image], "signature.png", { type: "image/png" });
    const layer = this.uiManager.currentLayer;
    if (!layer) { this.toast("Scroll to the page to sign, then try again."); return; }
    if (this.annotbar.hidden) this.openAnnotate();
    await layer.pasteEditor({ mode: pdfjs.AnnotationEditorType.STAMP }, { bitmapFile: file });
    this.toast("Drag the signature into place; use the corners to resize it.", 5000);
  }

  #signatureDialog() {
    const saved = store.get("pdfm-signature", null);
    const W = 520, H = 180, ratio = Math.min(window.devicePixelRatio || 1, 2);
    const pad = el("canvas", { class: "rd-sigpad", width: W * ratio, height: H * ratio, "aria-label": "Draw your signature here" });
    const ctx = pad.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.lineCap = ctx.lineJoin = "round"; ctx.lineWidth = 2.6; ctx.strokeStyle = "#111";
    let drawn = false, last = null;
    const point = (e) => { const r = pad.getBoundingClientRect(); return [(e.clientX - r.left) * W / r.width, (e.clientY - r.top) * H / r.height]; };
    pad.addEventListener("pointerdown", (e) => { pad.setPointerCapture(e.pointerId); last = point(e); drawn = true; ctx.beginPath(); ctx.arc(last[0], last[1], 1.2, 0, 7); ctx.fillStyle = "#111"; ctx.fill(); });
    pad.addEventListener("pointermove", (e) => {
      if (!last) return;
      const p = point(e);
      ctx.beginPath(); ctx.moveTo(...last); ctx.lineTo(...p); ctx.stroke();
      last = p;
    });
    pad.addEventListener("pointerup", () => { last = null; });
    const typed = el("input", { type: "text", class: "rd-sigtext", placeholder: "Or type your name", "aria-label": "Type your name", autocomplete: "name" });
    const clear = () => { ctx.clearRect(0, 0, W, H); drawn = false; };
    typed.addEventListener("input", () => {
      clear();
      if (!typed.value.trim()) return;
      ctx.fillStyle = "#111";
      let size = 64;
      do { ctx.font = `italic ${size}px "Segoe Script", "Brush Script MT", "Lucida Handwriting", "Apple Chancery", cursive`; size -= 4; }
      while (ctx.measureText(typed.value).width > W - 40 && size > 16);
      ctx.textBaseline = "middle"; ctx.textAlign = "center";
      ctx.fillText(typed.value, W / 2, H / 2);
      drawn = true;
    });
    const pick = el("input", { type: "file", accept: "image/*", hidden: true });
    let picked = null;
    pick.addEventListener("change", async () => {
      const f = pick.files[0];
      if (!f) return;
      const img = new Image();
      img.src = URL.createObjectURL(f);
      await img.decode().catch(() => {});
      clear();
      const s = Math.min((W - 20) / img.width, (H - 20) / img.height, 1);
      ctx.drawImage(img, (W - img.width * s) / 2, (H - img.height * s) / 2, img.width * s, img.height * s);
      URL.revokeObjectURL(img.src);
      drawn = true; picked = f;
    });
    const remember = el("input", { type: "checkbox", checked: true });
    const body = [
      el("p", {}, "Draw your signature with the mouse, pen or finger, type your name, or use a picture of your signature."),
      pad,
      el("div", { class: "rd-sigrow" }, typed,
        el("button", { type: "button", class: "rd-btn", onclick: () => pick.click() }, "Picture…"),
        el("button", { type: "button", class: "rd-btn", onclick: () => { typed.value = ""; clear(); } }, "Clear"), pick),
      el("label", { class: "rd-sigremember" }, remember, "Remember this signature on this computer"),
    ];
    const buttons = [["cancel", "Cancel"], ["ok", "Place signature", true]];
    if (saved) buttons.splice(1, 0, ["saved", "Use saved signature"]);
    return this.#dialog("Sign", body, buttons).then(async (answer) => {
      if (answer === "saved") return (await fetch(saved)).blob();
      if (answer !== "ok") return null;
      if (!drawn) { this.toast("Draw or type a signature first."); return null; }
      const blob = picked && !typed.value ? picked : await this.#trim(pad);
      if (remember.checked) {
        const reader = new FileReader();
        reader.onload = () => store.set("pdfm-signature", reader.result);
        reader.readAsDataURL(blob);
      }
      return blob;
    });
  }

  /** The drawn part of a canvas as a PNG with a transparent background. */
  async #trim(canvas) {
    const { width, height } = canvas;
    const data = canvas.getContext("2d").getImageData(0, 0, width, height).data;
    let x0 = width, y0 = height, x1 = -1, y1 = -1;
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      if (data[(y * width + x) * 4 + 3] > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    }
    const m = 6, out = document.createElement("canvas");
    x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m); x1 = Math.min(width - 1, x1 + m); y1 = Math.min(height - 1, y1 + m);
    out.width = Math.max(1, x1 - x0 + 1); out.height = Math.max(1, y1 - y0 + 1);
    out.getContext("2d").drawImage(canvas, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
    return new Promise((resolve) => out.toBlob(resolve, "image/png"));
  }

  // ---- dialogs ----------------------------------------------------------------
  #dialog(title, body, buttons) {
    return new Promise((resolve) => {
      const form = el("form", { method: "dialog" }, el("h3", {}, title), ...body,
        el("div", { class: "actions" }, ...buttons.map(([value, label, primary]) =>
          el("button", { value, class: primary ? "primary" : null, type: "submit" }, label))));
      const dialog = el("dialog", { class: "rd-dialog" }, form);
      dialog.addEventListener("close", () => { resolve(dialog.returnValue); dialog.remove(); });
      dialog.addEventListener("cancel", () => { dialog.returnValue = "cancel"; });
      this.root.append(dialog);
      dialog.showModal();
    });
  }

  async #askPassword(wrong) {
    const input = el("input", { type: "password", "aria-label": "Password", autocomplete: "off" });
    const body = [el("p", {}, `“${this.name}” is protected. Enter its password to open it.`), input];
    if (wrong) body.push(el("div", { class: "err", role: "alert" }, "That password is incorrect."));
    const pending = this.#dialog("Password required", body, [["cancel", "Cancel"], ["ok", "Open", true]]);
    input.focus();
    const answer = await pending;
    return answer === "ok" ? input.value : null;
  }

  async showProperties() {
    if (!this.doc) return;
    const { info, metadata } = await this.doc.getMetadata();
    const page = await this.doc.getPage(this.pdfViewer.currentPageNumber);
    const [x1, y1, x2, y2] = page.view;
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    const date = (raw) => {
      const d = raw && pdfjs.PDFDateString.toDateObject(raw);
      return d ? d.toLocaleString() : "";
    };
    const size = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
    // -- accessibility -- tagged: says so (MarkInfo) or has a structure tree (pdf.js gives MarkInfo as a Map)
    const mark = await this.doc.getMarkInfo().catch(() => null);
    const tagged = (mark instanceof Map ? mark.get("Marked") : mark?.Marked) || !!(await page.getStructTree().catch(() => null));
    // -- end of accessibility --
    const rows = [
      ["File", this.activeTab?.path || this.name], ["File size", size(this.bytes?.length ?? this.activeTab?.size ?? 0)],
      ["Title", metadata?.get("dc:title") || info.Title], ["Author", info.Author], ["Subject", info.Subject],
      ["Keywords", info.Keywords], ["Created", date(info.CreationDate)], ["Modified", date(info.ModDate)],
      ["Application", info.Creator], ["PDF producer", info.Producer], ["PDF version", info.PDFFormatVersion],
      ["Pages", String(this.doc.numPages)],
      ["Tagged (accessible)", tagged ? "Yes" : "No"], ["Language", info.Language || "Not set"],  // -- accessibility --
      ["Page size", `${(w / 72 * 25.4).toFixed(0)} × ${(h / 72 * 25.4).toFixed(0)} mm (${(w / 72).toFixed(2)} × ${(h / 72).toFixed(2)} in)`],
      ["Fast web view", info.IsLinearized ? "Yes" : "No"],
      ["Form", info.IsAcroFormPresent ? "Yes" : info.IsXFAPresent ? "Yes (XFA)" : "No"],
      ["Password protected", this.password ? "Yes" : "No"],
    ].filter(([, v]) => v);
    // -- limits and attached files --
    const perms = await this.doc.getPermissions().catch(() => null);
    if (perms) rows.push(["Limits", permissionText(perms)]);
    const attached = Object.values((await this.doc.getAttachments().catch(() => null)) || {});
    const dl = el("dl", { class: "rd-props" });
    for (const [k, v] of rows) dl.append(el("dt", {}, k), el("dd", {}, v));
    const parts = [dl];
    if (attached.length) {
      parts.push(el("h3", {}, `Attached files (${attached.length})`), el("ul", { class: "rd-attached" },
        ...attached.map((a) => el("li", {}, el("span", {}, a.filename, a.description ? el("small", {}, " — " + a.description) : null),
          el("small", {}, size(a.content?.length || 0)),
          el("button", { type: "button", class: "rd-btn", onclick: () => this.#saveBytes(a.content, a.filename, () => {}, this.activeTab?.fileId) }, "Save…")))));
    }
    // -- end of limits and attached files --
    await this.#dialog("Document properties", parts, [["ok", "Close", true]]);
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
      body.append("file", new Blob([await this.#fullBytes()], { type: "application/pdf" }), "file.pdf");
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
    const answer = await this.#dialog("Accessibility", body, [...(fix ? [["tag", "Make accessible…"]] : []), ["ok", "Close", true]]);
    if (answer === "tag") this.#edit("tag");
  }
  // -- end of accessibility --

  toast(message, ms = 4000, action = null) {
    if (!message) return;
    if (this.toastEl) this.toastEl.remove();
    const t = el("div", { class: "rd-toast", role: "status" }, el("span", {}, message));
    if (action) t.append(el("button", { type: "button", onclick: action[1] }, action[0]));
    if (this.scalebar && !this.scalebar.hidden) {  // over the drawing's top, not its title block
      t.classList.add("top");
      t.style.top = `${this.body.offsetTop + 12}px`;
    }
    this.root.append(t);
    this.toastEl = t;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => t.remove(), ms);
  }

  // ---- changing words on the page ----------------------------------------------------
  /** Edit text tool: a click on a line of text opens a box to retype it. */
  #textClick(e) {
    if (!this.textEditMode || e.button !== 0) return;
    if (e.target.closest?.(".rd-textedit-box")) return;  // inside the open editor
    const pageEl = e.target.closest?.(".page");
    const pageNumber = Number(pageEl?.dataset.pageNumber);
    if (!pageNumber || window.getSelection()?.toString().length > 1) return;
    const span = e.target.closest?.(".textLayer span");
    e.preventDefault();
    e.stopPropagation();
    // The words that show at the click (not old ones covered by a change).
    const line = span && !span.classList.contains("markedContent") && span.textContent.trim()
      ? this.#lineOf(span, pageEl, e.clientX, e.clientY) : this.#lineOf(null, pageEl, e.clientX, e.clientY);
    if (line) {
      const at = line.spans.find((it) => it.r.left <= e.clientX && e.clientX <= it.r.right) || line.spans[0];
      this.#openTextEditor(at.s, pageEl, pageNumber, e.shiftKey ? "paragraph" : "line");
    } else {
      this.#openNewText(e, pageEl, pageNumber);  // an empty spot: new words there
    }
  }

  /** The lines of text on a page as drawn: [{spans, left, top, right, bottom, h}],
   *  top to bottom. Words next to each other share a line; a far-off column doesn't. */
  /** The page's words that show: [{s: span, r: its box}]. A change covers
   *  the old words and writes them again later on the page; the old ones
   *  are still in the file (and in pdf.js's text) under the new ones. */
  #shownSpans(pageEl) {
    const all = [...pageEl.querySelectorAll(".textLayer span")]
      .filter((s) => !s.classList.contains("markedContent") && s.textContent.trim())
      .map((s) => ({ s, r: s.getBoundingClientRect() }));
    if (all.length > 4000) return all;  // a drawing full of labels: take them all
    return all.filter((it, i) => {
      const area = it.r.width * it.r.height;
      if (!area) return false;
      let under = 0;
      for (let j = i + 1; j < all.length; j++) {
        const o = all[j].r;
        const w = Math.min(it.r.right, o.right) - Math.max(it.r.left, o.left);
        const h = Math.min(it.r.bottom, o.bottom) - Math.max(it.r.top, o.top);
        if (w > 0 && h > it.r.height * 0.5) under += w * h;
      }
      return under < area * 0.5;
    });
  }

  /** The words on the line at a span (or a point): next to each other,
   *  without the gap of another column. Or null. */
  #lineOf(span, pageEl, x, y) {
    const lines = this.#pageLines(pageEl, 1.2);
    return lines.find((l) => l.spans.some((it) => it.s === span))
      || lines.find((l) => x >= l.left - 2 && x <= l.right + 2 && y >= l.top - 1 && y <= l.bottom + 1) || null;
  }

  #pageLines(pageEl, apart = 2.5) {
    const items = this.#shownSpans(pageEl)
      .sort((a, b) => (a.r.top + a.r.bottom) / 2 - (b.r.top + b.r.bottom) / 2 || a.r.left - b.r.left);
    const lines = [];
    for (const it of items) {
      const h = it.r.height, cy = (it.r.top + it.r.bottom) / 2;
      const line = lines.find((l) => Math.abs((l.top + l.bottom) / 2 - cy) < Math.min(l.h, h) * 0.5
        && it.r.left - l.right < Math.max(l.h, h) * apart && l.left - it.r.right < Math.max(l.h, h) * apart);
      if (line) {
        line.spans.push(it);
        Object.assign(line, { left: Math.min(line.left, it.r.left), right: Math.max(line.right, it.r.right),
          top: Math.min(line.top, it.r.top), bottom: Math.max(line.bottom, it.r.bottom), h: Math.max(line.h, h) });
      } else {
        lines.push({ spans: [it], left: it.r.left, right: it.r.right, top: it.r.top, bottom: it.r.bottom, h });
      }
    }
    for (const l of lines) l.spans.sort((a, b) => a.r.left - b.r.left);
    return lines.sort((a, b) => a.top - b.top);
  }

  static #middle(node) {
    const r = node.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  }

  static #lineText(line) {
    let text = "";
    line.spans.forEach((it, i) => {
      const prev = line.spans[i - 1];
      if (prev && !/\s$/.test(text) && !/^\s/.test(it.s.textContent) && it.r.left - prev.r.right > line.h * 0.12) text += " ";
      text += it.s.textContent;
    });
    return text.replace(/\s+/g, " ").trim();
  }

  /** The paragraph around ``span``: the lines above and below it set the same way. */
  #paragraphOf(span, pageEl) {
    const lines = this.#pageLines(pageEl);
    let at = lines.findIndex((l) => l.spans.some((it) => it.s === span));
    if (at < 0) return null;
    // Lines of one paragraph: close below each other, in one column, and in
    // the same type (a heading or a differently set paragraph is another).
    const type = (l) => {
      const st = l.spans[0].s.style;
      return [parseFloat(st.getPropertyValue("--font-height")) || l.h, st.fontFamily];
    };
    const close = (a, b) => {
      const gap = b.top - a.bottom, h = Math.min(a.h, b.h);
      const [fa, ka] = type(a), [fb, kb] = type(b);
      return gap > -h * 0.3 && gap < h * 1.1 && Math.abs(a.h - b.h) < h * 0.35
        && Math.abs(fa - fb) <= Math.max(fa, fb) * 0.08 && ka === kb
        && b.left < a.right && b.right > a.left && Math.abs(b.left - a.left) < h * 3;
    };
    let first = at, last = at;
    // A paragraph is a column of lines: only neighbours in that column count.
    const column = (l) => l.left < lines[at].right && l.right > lines[at].left;
    const above = (i) => { for (let j = i - 1; j >= 0; j--) if (column(lines[j])) return j; return -1; };
    const below = (i) => { for (let j = i + 1; j < lines.length; j++) if (column(lines[j])) return j; return -1; };
    for (let j = above(first); j >= 0 && close(lines[j], lines[first]); j = above(first)) first = j;
    for (let j = below(last); j >= 0 && close(lines[last], lines[j]); j = below(last)) last = j;
    const chosen = lines.slice(first, last + 1).filter((l, i, all) => i === 0 || i === all.length - 1 || column(l));
    let text = "";
    for (const line of chosen) {
      const words = Reader.#lineText(line);
      if (/[a-z]-$/.test(text) && /^[a-z]/.test(words)) text = text.slice(0, -1) + words;  // a word broken over two lines
      else text += (text ? " " : "") + words;
    }
    return {
      text, left: Math.min(...chosen.map((l) => l.left)), right: Math.max(...chosen.map((l) => l.right)),
      top: chosen[0].top, bottom: chosen.at(-1).bottom, h: lines[at].h, lines: chosen.length,
      first: chosen[0].spans[0].s,  // its first words, for the look
      lead: chosen.length > 1 ? (chosen.at(-1).top - chosen[0].top) / (chosen.length - 1) : lines[at].h * 1.2,
    };
  }

  /** A box on the page for typing: [wrapper, input or textarea]. */
  /** How words of the page look on screen, to type over them in place:
   *  {family, size, left, baseline (screen px), color, background}, from
   *  the text pdf.js read (its font is the document's own), or null. */
  async #textLook(span, pageNumber) {
    try {
      const view = this.pdfViewer.getPageView(pageNumber - 1);
      if (!view?.pdfPage || view.viewport.rotation % 360 !== 0) return null;
      this.textContents ||= new WeakMap();
      let content = this.textContents.get(view.pdfPage);
      if (!content) {
        content = await view.pdfPage.getTextContent();
        this.textContents.set(view.pdfPage, content);
      }
      const layer = span.closest(".textLayer")?.getBoundingClientRect();
      if (!layer || !span.isConnected) return null;
      const r = span.getBoundingClientRect();
      const [a, b, c, d, e, f] = view.viewport.transform;
      let best = null, far = Infinity;
      for (const item of content.items) {
        if (!item.str || item.str !== span.textContent) continue;
        const [ia, ib, ic, id, ie, iff] = item.transform;
        const tx = [a * ia + c * ib, b * ia + d * ib, a * ic + c * id, b * ic + d * id, a * ie + c * iff + e, b * ie + d * iff + f];
        const size = Math.hypot(tx[2], tx[3]);
        if (Math.abs(tx[1]) > size * 0.01 || Math.abs(tx[2]) > size * 0.01) continue;  // not level
        const dist = Math.abs(layer.left + tx[4] - r.left) + Math.abs(layer.top + tx[5] - (r.top + r.height * 0.8));
        if (dist < far) { far = dist; best = { item, tx, size }; }
      }
      if (!best || far > best.size) return null;
      const { item, tx, size } = best;
      const generic = content.styles[item.fontName]?.fontFamily || "sans-serif";
      let real = null;  // the font's own name, for the copy installed on this computer
      try { if (view.pdfPage.commonObjs.has(item.fontName)) real = view.pdfPage.commonObjs.get(item.fontName); } catch (err) { /* not read yet */ }
      const name = String(real?.name || "").replace(/^[A-Z]{6}\+/, "");
      const bold = !!(real?.bold || real?.black) || /bold|black|heavy|semibold|demi/i.test(name);
      const italic = !!real?.italic || /italic|oblique/i.test(name);
      const look = { size, left: layer.left + tx[4], baseline: layer.top + tx[5], width: r.width, fontName: name,
        weight: bold ? "bold" : "", style: italic ? "italic" : "", ...this.#pageColours(span.closest(".page"), r) };
      // The document's font as pdf.js loaded it shows the words just as on the
      // page, unless it mixed up its letters (fonts cut down for a PDF can
      // map a letter to the wrong shape); so it's checked against the page,
      // then the same font installed here, then a plain one of its kind.
      const candidates = [];
      if ([...document.fonts].some((ff) => ff.family.replace(/["']/g, "") === item.fontName && ff.status === "loaded")) {
        candidates.push({ family: `"${item.fontName}", ${generic}`, weight: "", style: "" });
      }
      const base = name.replace(/[-,]?(bold|italic|oblique|regular|roman|book|semibold|black|medium|light|mt|ps)+$/gi, "").replace(/(ps)?mt$/i, "");
      for (const n of new Set([base.replace(/([a-z])([A-Z])/g, "$1 $2"), base, name.replace(/[-,]/g, " ")])) {
        if (n && /^[\w .]+$/.test(n)) candidates.push({ family: `"${n}", ${generic}`, weight: look.weight, style: look.style });
      }
      const pageEl = span.closest(".page");
      for (const c of candidates) {
        if (this.#inkMatches(pageEl, { ...look, ...c }, span.textContent, r)) return { ...look, ...c };
      }
      return { ...look, family: generic };
    } catch (err) {
      return null;  // the plain box will do
    }
  }

  /** The paper and ink colours around a box on the page as drawn. */
  #pageColours(pageEl, r) {
    const out = { background: "#fff", color: "#111" };
    try {
      const canvas = pageEl?.querySelector("canvas");
      if (!canvas) return out;
      const cr = canvas.getBoundingClientRect(), k = canvas.width / cr.width;
      const x0 = Math.max(0, Math.floor((r.left - cr.left) * k) - 3), y0 = Math.max(0, Math.floor((r.top - cr.top) * k) - 3);
      const w = Math.min(canvas.width - x0, Math.ceil(r.width * k) + 6), h = Math.min(canvas.height - y0, Math.ceil(r.height * k) + 6);
      if (w < 2 || h < 2) return out;
      const px = canvas.getContext("2d").getImageData(x0, y0, w, h).data;
      const edge = [];
      for (let x = 0; x < w; x++) edge.push((x) * 4, ((h - 1) * w + x) * 4);
      for (let y = 0; y < h; y++) edge.push(y * w * 4, (y * w + w - 1) * 4);
      const mid = (ch) => { const v = edge.map((i) => px[i + ch]).sort((p, q) => p - q); return v[v.length >> 1]; };
      const bg = [mid(0), mid(1), mid(2)];
      let ink = null, most = 60;
      for (let i = 0; i < px.length; i += 4) {
        const dist = Math.abs(px[i] - bg[0]) + Math.abs(px[i + 1] - bg[1]) + Math.abs(px[i + 2] - bg[2]);
        if (dist > most) { most = dist; ink = [px[i], px[i + 1], px[i + 2]]; }
      }
      out.background = `rgb(${bg.join(" ")})`;
      if (ink) out.color = `rgb(${ink.join(" ")})`;
    } catch (err) { /* a canvas that can't be read: black on white */ }
    return out;
  }

  /** The spacing that makes ``text`` in a font as wide as on the page:
   *  {letter, word} in px, or {}. */
  #fitSpacing(text, look, width) {
    const natural = this.#textWidth(text, look), spaces = text.split(" ").length - 1;
    const diff = width - natural;
    if (Math.abs(diff) <= 0.5 || text.length < 2) return {};
    return diff > 0 && spaces ? { word: Math.min(diff / spaces, look.size * 1.5) }
      : { letter: Math.max(-look.size * 0.25, Math.min(diff / (text.length - 1), look.size * 0.25)) };
  }

  /** Whether ``text`` drawn in the look's font lands on the page's own ink
   *  in the box ``r`` (same letters, same shapes). */
  #inkMatches(pageEl, look, text, r) {
    try {
      const canvas = pageEl?.querySelector("canvas");
      if (!canvas || !text.trim()) return false;
      const cr = canvas.getBoundingClientRect(), k = canvas.width / cr.width;
      const x0 = Math.max(0, Math.floor((r.left - cr.left) * k)), y0 = Math.max(0, Math.floor((r.top - cr.top) * k));
      const w = Math.min(canvas.width - x0, Math.ceil(r.width * k) + 2), h = Math.min(canvas.height - y0, Math.ceil(r.height * k) + 2);
      if (w < 4 || h < 4) return false;
      const page = canvas.getContext("2d").getImageData(x0, y0, w, h).data;
      const off = document.createElement("canvas");
      off.width = w; off.height = h;
      const c = off.getContext("2d", { willReadFrequently: true });
      const spacing = this.#fitSpacing(text, look, r.width);
      c.font = `${look.style || ""} ${look.weight || ""} ${look.size * k}px ${look.family}`;
      if ("letterSpacing" in c) c.letterSpacing = `${(spacing.letter || 0) * k}px`;
      if ("wordSpacing" in c) c.wordSpacing = `${(spacing.word || 0) * k}px`;
      c.fillText(text, (look.left - cr.left) * k - x0, (look.baseline - cr.top) * k - y0);
      const ours = c.getImageData(0, 0, w, h).data;
      const lum = (d, i) => d[i] * 0.3 + d[i + 1] * 0.59 + d[i + 2] * 0.11;
      let bg = 0;
      for (let x = 0; x < w; x++) bg += lum(page, x * 4) + lum(page, ((h - 1) * w + x) * 4);
      bg /= 2 * w;
      const inkA = new Uint8Array(w * h), inkB = new Uint8Array(w * h);
      for (let i = 0; i < w * h; i++) {
        inkA[i] = Math.abs(lum(page, i * 4) - bg) > 70 ? 1 : 0;
        inkB[i] = ours[i * 4 + 3] > 110 ? 1 : 0;
      }
      const near = (ink, i) => {  // ink within a pixel
        const x = i % w, y = (i / w) | 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx, yy = y + dy;
          if (xx >= 0 && yy >= 0 && xx < w && yy < h && ink[yy * w + xx]) return true;
        }
        return false;
      };
      let a = 0, b = 0, ab = 0, ba = 0;
      for (let i = 0; i < w * h; i++) {
        if (inkA[i]) { a++; if (near(inkB, i)) ab++; }
        if (inkB[i]) { b++; if (near(inkA, i)) ba++; }
      }
      return a > 0 && b > 0 && ab / a > 0.96 && ba / b > 0.96;
    } catch (err) {
      return false;
    }
  }

  /** Where the baseline sits in a line box of ``lineHeight`` (px from its top). */
  static #baselineIn(look, lineHeight) {
    const probe = el("div", {}, "Hg", el("span"));
    Object.assign(probe.style, { position: "absolute", visibility: "hidden", left: "-9999px", top: "0", whiteSpace: "pre",
      fontFamily: look.family, fontSize: `${look.size}px`, fontWeight: look.weight || "", fontStyle: look.style || "",
      lineHeight: `${lineHeight}px` });
    Object.assign(probe.lastChild.style, { display: "inline-block", width: "0", height: "0", verticalAlign: "baseline" });
    document.body.append(probe);
    const at = probe.lastChild.offsetTop;
    probe.remove();
    return at;
  }

  /** How wide ``text`` is in a font, in px. */
  #textWidth(text, look) {
    this.measureCtx ||= document.createElement("canvas").getContext("2d");
    this.measureCtx.font = `${look.style || ""} ${look.weight || ""} ${look.size}px ${look.family}`;
    return this.measureCtx.measureText(text).width;
  }

  /** A box on the page for typing: [wrapper, input or textarea]. With a
   *  ``look``, the words in it sit exactly on the page's own: same font,
   *  size, spacing, colour and baseline, so nothing moves as you type. */
  #editorBox(pageEl, { left, top, width, height, fontSize, fontFamily, multiline, value, label, hint, look, lineHeight }) {
    const page = pageEl.getBoundingClientRect();
    const field = multiline
      ? el("textarea", { class: "rd-textedit", spellcheck: "true", "aria-label": label })
      : el("input", { type: "text", class: "rd-textedit", spellcheck: "true", "aria-label": label });
    field.value = value;
    const box = el("div", { class: "rd-textedit-box" }, field, el("div", { class: "rd-textedit-hint" }, ...hint));
    if (look) {
      const lh = lineHeight || Math.round(look.size * 1.3 * 100) / 100;
      const spacing = look.spacing || {};
      field.classList.add("rd-textedit-fit");
      Object.assign(field.style, { fontFamily: look.family, fontSize: `${look.size}px`, lineHeight: `${lh}px`,
        fontWeight: look.weight || "", fontStyle: look.style || "",
        color: look.color, background: look.background, width: `${width}px`, height: `${multiline ? height : lh}px`,
        letterSpacing: spacing.letter ? `${spacing.letter}px` : "", wordSpacing: spacing.word ? `${spacing.word}px` : "" });
      left = look.left;
      top = look.baseline - Reader.#baselineIn(look, lh);
      Object.assign(box.style, { left: `${left - page.left - pageEl.clientLeft}px`, top: `${top - page.top - pageEl.clientTop}px` });
      if (!multiline) {  // grows with the words
        const grow = () => {
          const t = field.value;
          const w = this.#textWidth(t, look) + (spacing.letter || 0) * t.length
            + (spacing.word || 0) * (t.split(" ").length - 1) + look.size;
          field.style.width = `${Math.max(width, Math.ceil(w))}px`;
        };
        field.addEventListener("input", grow);
        grow();
      }
      return [box, field];
    }
    Object.assign(field.style, { width: `${width}px`, height: `${height}px`, fontSize: `${fontSize}px`, fontFamily: fontFamily || "" });
    Object.assign(box.style, { left: `${left - page.left - pageEl.clientLeft - 3}px`, top: `${top - page.top - pageEl.clientTop - 2}px` });
    return [box, field];
  }

  async #openTextEditor(span, pageEl, pageNumber, mode = "line") {
    this.#closeTextEditor(false);
    const ticket = (this.editTicket = (this.editTicket || 0) + 1);
    const viewport = this.pdfViewer.getPageView(pageNumber - 1).viewport;
    const sideways = viewport.rotation % 180 !== 0;
    const paragraph = mode === "paragraph" && !sideways ? this.#paragraphOf(span, pageEl) : null;
    const first = paragraph ? paragraph.first
      : (!sideways && this.#lineOf(span, pageEl, ...Reader.#middle(span))?.spans[0].s) || span;
    const look = await this.#textLook(first, pageNumber);
    if (ticket !== this.editTicket || !span.isConnected || !this.textEditMode) return;  // clicked elsewhere meanwhile
    const layer = pageEl.querySelector(".textLayer").getBoundingClientRect();
    const r = span.getBoundingClientRect();
    // A line is all its words, however pdf.js split them up.
    const line = !paragraph && !sideways ? this.#lineOf(span, pageEl, r.left + r.width / 2, r.top + r.height / 2) : null;
    const area = paragraph || line || { left: r.left, right: r.right, top: r.top, bottom: r.bottom, h: sideways ? r.width : r.height };
    const original = paragraph ? paragraph.text : line ? Reader.#lineText(line) : span.textContent;
    // With the view turned, the line runs up or down the screen: size the box
    // from the line's own length and height.
    const lineW = sideways ? r.height : area.right - area.left, lineH = area.h;
    // The page's spacing, so the typed words line up with the page's to the end.
    if (look && !paragraph) look.spacing = this.#fitSpacing(original, look, area.right - look.left);
    const toggle = el("button", { type: "button", class: "rd-textedit-mode", title: paragraph ? "Change this line only" : "Rewrite the whole paragraph (or Shift+click a line)" },
      paragraph ? "One line" : "¶ Whole paragraph");
    toggle.disabled = sideways;
    const lead = paragraph ? paragraph.lead : null;
    const [box, field] = this.#editorBox(pageEl, {
      left: area.left, top: area.top, multiline: !!paragraph, value: original, look, lineHeight: lead,
      width: look ? (paragraph ? lineW + look.size * 0.6 : Math.max(lineW + look.size, 80)) : Math.max(lineW + (paragraph ? 16 : 40), 80),
      height: paragraph ? (look ? lead * (paragraph.lines + 1) : area.bottom - area.top + lineH * 1.5) : lineH + 4,
      fontSize: Math.max(8, lineH * 0.8), fontFamily: getComputedStyle(span).fontFamily,
      label: paragraph ? "New paragraph (Ctrl+Enter to change, Esc to cancel)" : "New text (Enter to change, Esc to cancel)",
      hint: [el("span", {}, paragraph ? "Ctrl+Enter to change · Esc to cancel" : "Enter to change · Esc to cancel"), toggle],
    });
    // The words' box in PDF points (measured from the page as drawn).
    const [ax, ay] = viewport.convertToPdfPoint(area.left - layer.left, area.top - layer.top);
    const [bx, by] = viewport.convertToPdfPoint(area.right - layer.left, area.bottom - layer.top);
    const rect = [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];
    toggle.addEventListener("pointerdown", (e) => e.preventDefault());  // keeps the field's focus
    toggle.addEventListener("click", () => {
      this.#closeTextEditor(false);
      this.#openTextEditor(span, pageEl, pageNumber, paragraph ? "line" : "paragraph");
    });
    // The words as shown go along, so only what was changed is drawn again.
    const picked = this.#fontPicker(box, field, look, viewport, { paragraph: !!paragraph, size: lineH });
    this.#startEditing(box, field, pageEl, original, (text) =>
      this.#applyTextEdit({ page: pageNumber, rect, text, ...(paragraph ? { paragraph: true } : { old: original }),
        ...picked.choices() }), picked.changed);
  }

  /** New words where the page was clicked; they take the look of the nearest text. */
  async #openNewText(e, pageEl, pageNumber) {
    this.#closeTextEditor(true);
    const ticket = (this.editTicket = (this.editTicket || 0) + 1);
    const at = { clientX: e.clientX, clientY: e.clientY };
    const viewport = this.pdfViewer.getPageView(pageNumber - 1).viewport;
    let near = null, best = Infinity;
    for (const line of this.#pageLines(pageEl)) {
      const d = Math.hypot(Math.max(line.left - at.clientX, 0, at.clientX - line.right), Math.max(line.top - at.clientY, 0, at.clientY - line.bottom) * 2);
      if (d < best) { best = d; near = line; }
    }
    const look = near ? await this.#textLook(near.spans[0].s, pageNumber) : null;
    if (ticket !== this.editTicket || !pageEl.isConnected || !this.textEditMode) return;
    const layer = pageEl.querySelector(".textLayer")?.getBoundingClientRect() || pageEl.getBoundingClientRect();
    if (look) {  // the new words' top left at the click, as they'll be drawn
      Object.assign(look, { left: at.clientX, baseline: at.clientY + look.size * 0.8 },
        this.#pageColours(pageEl, { left: at.clientX, top: at.clientY, width: 40, height: look.size }));
      if (near) look.color = this.#pageColours(pageEl, near.spans[0].r).color;
    }
    const h = near ? near.h : 16 * viewport.scale;
    const [box, field] = this.#editorBox(pageEl, {
      left: at.clientX, top: at.clientY, width: look ? Math.max(160, look.size * 8) : 240, height: look ? look.size * 1.2 : h + 4,
      fontSize: Math.max(8, h * 0.8), multiline: true, lineHeight: look ? look.size * 1.2 : null,
      fontFamily: near ? getComputedStyle(near.spans[0].s).fontFamily : "", value: "", look,
      label: "New text (Enter to add, Shift+Enter for a new line, Esc to cancel)",
      hint: [el("span", {}, "New text · Enter to add · Shift+Enter new line · Esc to cancel")],
    });
    field.dataset.enter = "apply";  // Enter adds the words; Shift+Enter starts a new line
    field.rows = 1;
    const [x, y] = viewport.convertToPdfPoint(at.clientX - layer.left, at.clientY - layer.top);
    const picked = this.#fontPicker(box, field, look, viewport, { newText: true, size: h });
    this.#startEditing(box, field, pageEl, "", (text) => this.#applyTextEdit({ page: pageNumber, at: [x, y], text,
      ...picked.choices() }));
  }

  #startEditing(box, field, pageEl, original, apply, restyled = null) {
    let done = false;
    const finish = (keep) => {
      if (done) return;
      done = true;
      if (this.textEditor?.box === box) this.textEditor = null;
      if (keep && (field.value !== original || restyled?.()) && (original || field.value.trim())) {
        // The typed words stay showing where they go until the page is
        // drawn again with them, so nothing flickers back to the old ones.
        box.classList.add("rd-pending");
        field.readOnly = true;
        field.blur();
        Promise.resolve(apply(field.value)).finally(() => box.remove());
      } else {
        box.remove();
      }
    };
    field.addEventListener("keydown", (e) => {
      e.stopPropagation();
      const multiline = field.tagName === "TEXTAREA";
      if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey || (field.dataset.enter === "apply" && !e.shiftKey))) { e.preventDefault(); finish(true); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    field.addEventListener("blur", () => setTimeout(() => { if (!done && !box.contains(document.activeElement)) finish(true); }));
    pageEl.append(box);
    this.textEditor = { box, finish };
    field.focus();
    field.select();
  }

  #closeTextEditor(keep) { this.textEditor?.finish(keep); }

  // -- text font picker and reflow --
  /** The bar above the text editor to choose how the words look: font,
   *  size, bold, italic, colour and (for more than a word) alignment. The
   *  field shows the choice as it's made. Returns {choices(): what changed,
   *  as the server takes it, changed(): whether anything did}. */
  #fontPicker(box, field, look, viewport, { paragraph = false, newText = false, size = 16 } = {}) {
    const scale = viewport.scale || 1;
    const start = {
      font: "", size: Math.round(((look?.size || size * 0.8) / scale) * 2) / 2,
      bold: look?.weight === "bold", italic: look?.style === "italic",
      color: Reader.#hexColour(look?.color || "#000000"), align: "",
    };
    const now = { ...start };
    const own = Reader.#familyOf(look?.fontName || "");
    const fontSel = el("select", { class: "rd-tf-font", title: "Font", "aria-label": "Font" },
      el("option", { value: "" }, own ? `${own} (as is)` : "Same font"));
    const sizeIn = el("input", { class: "rd-tf-size", type: "number", min: "1", max: "500", step: "0.5", value: String(start.size),
      title: "Size (points)", "aria-label": "Font size in points" });
    const toggle = (label, title, key) => el("button", { type: "button", class: `rd-tf-btn rd-tf-${key}`, title,
      "aria-label": title, "aria-pressed": String(now[key]) }, label);
    const boldBtn = toggle("B", "Bold (Ctrl+B)", "bold"), italicBtn = toggle("I", "Italic (Ctrl+I)", "italic");
    const colorIn = el("input", { class: "rd-tf-color", type: "color", value: start.color, title: "Colour", "aria-label": "Text colour" });
    const aligns = [["left", "Align left", "M4 6h16M4 10h10M4 14h16M4 18h10"], ["center", "Centre", "M4 6h16M7 10h10M4 14h16M7 18h10"],
      ["right", "Align right", "M4 6h16M10 10h10M4 14h16M10 18h10"], ["justify", "Justify", "M4 6h16M4 10h16M4 14h16M4 18h16"]]
      .map(([value, title, d]) => {
        const b = el("button", { type: "button", class: "rd-tf-btn rd-tf-align", title, "aria-label": title, "aria-pressed": "false",
          html: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>` });
        b.dataset.align = value;
        return b;
      });
    const alignGroup = el("span", { class: "rd-tf-group", role: "group", "aria-label": "Alignment" }, ...aligns);
    aligns[3].hidden = !paragraph && !newText;  // one line: set within its old width; justifying needs more lines
    const bar = el("div", { class: "rd-tf-bar", role: "toolbar", "aria-label": "Text look" },
      fontSel, sizeIn, el("span", { class: "rd-tf-group" }, boldBtn, italicBtn), colorIn, alignGroup);
    box.append(bar);

    const generic = /serif|times|roman|georgia|garamond|cambria|book|palatino/i.test(own) && !/sans/i.test(own) ? "serif"
      : /mono|courier|consol/i.test(own) ? "monospace" : "sans-serif";
    const original = look ? { family: look.family, size: look.size, weight: look.weight, style: look.style, color: look.color } : null;
    const lineHeight = parseFloat(field.style.lineHeight) || 0;
    const show = () => {
      const k = now.size / start.size;
      const family = now.font ? Reader.#cssFamily(now.font, generic) : original?.family || "";
      if (look) Object.assign(look, { family, size: original.size * k, weight: now.bold ? "bold" : "", style: now.italic ? "italic" : "" });
      Object.assign(field.style, {
        fontFamily: family, fontWeight: now.bold ? "bold" : "normal", fontStyle: now.italic ? "italic" : "normal",
        color: now.color !== start.color || !original ? now.color : original.color, textAlign: now.align === "justify" ? "justify" : now.align || "",
      });
      if (look) field.style.fontSize = `${look.size}px`;
      else field.style.fontSize = `${Math.max(8, now.size * scale)}px`;
      if (lineHeight) field.style.lineHeight = `${lineHeight * k}px`;
      if (now.font || now.bold !== start.bold || now.italic !== start.italic) {
        field.style.letterSpacing = field.style.wordSpacing = "";  // the page's spacing was for its own font
      }
      if (paragraph && lineHeight) field.style.height = `${Math.max(parseFloat(field.style.height) || 0, lineHeight * k * 2)}px`;
      fit();
    };
    // New words: the box grows with the longest line and the number of lines.
    const fit = () => {
      if (!newText) return field.dispatchEvent(new Event("input"));
      const lines = field.value.split("\n");
      const m = look || { family: getComputedStyle(field).fontFamily, size: parseFloat(field.style.fontSize) || 16 };
      const w = Math.max(...lines.map((t) => this.#textWidth(t, m)), 0) + m.size;
      const lh = parseFloat(field.style.lineHeight) || m.size * 1.25;
      Object.assign(field.style, { width: `${Math.max(look ? look.size * 4 : 160, Math.ceil(w))}px`, height: `${Math.ceil(lh * lines.length + 2)}px` });
    };
    if (newText) field.addEventListener("input", fit);

    const press = () => {
      boldBtn.setAttribute("aria-pressed", String(now.bold));
      italicBtn.setAttribute("aria-pressed", String(now.italic));
      for (const b of aligns) b.setAttribute("aria-pressed", String(b.dataset.align === now.align));
      show();
    };
    for (const b of [boldBtn, italicBtn, ...aligns]) b.addEventListener("pointerdown", (e) => e.preventDefault());  // the field keeps the focus
    boldBtn.addEventListener("click", () => { now.bold = !now.bold; press(); });
    italicBtn.addEventListener("click", () => { now.italic = !now.italic; press(); });
    for (const b of aligns) b.addEventListener("click", () => { now.align = now.align === b.dataset.align ? "" : b.dataset.align; press(); });
    fontSel.addEventListener("change", () => { now.font = fontSel.value; show(); });
    sizeIn.addEventListener("input", () => {
      const v = parseFloat(sizeIn.value);
      if (v >= 1 && v <= 500) { now.size = v; show(); }
    });
    colorIn.addEventListener("input", () => { now.color = colorIn.value; show(); });
    field.addEventListener("keydown", (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === "b" || key === "i") {
        e.preventDefault();
        now[key === "b" ? "bold" : "italic"] = !now[key === "b" ? "bold" : "italic"];
        press();
      }
    });
    // The bar's own keys: Enter applies, Esc cancels; nothing reaches the reader's shortcuts.
    bar.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") { e.preventDefault(); this.textEditor?.finish(false); }
      if (e.key === "Enter" && e.target.tagName !== "BUTTON") { e.preventDefault(); this.textEditor?.finish(true); }
    });
    // Leaving the bar for the page is like leaving the field: the change is made.
    bar.addEventListener("focusout", () => setTimeout(() => {
      if (box.isConnected && !box.contains(document.activeElement) && this.textEditor?.box === box) this.textEditor.finish(true);
    }));

    this.#fontChoices().then((choices) => {
      if (!choices || !fontSel.isConnected) return;
      const ownKey = Reader.#familyKey(own);
      const group = (label, names) => {
        const items = (names || []).filter((n) => Reader.#familyKey(n) !== ownKey);
        if (items.length) fontSel.append(el("optgroup", { label }, ...items.map((n) => el("option", { value: n }, n))));
      };
      group("In this document", choices.document);
      group("On this computer", choices.installed);
      group("More on this computer", choices.more);
      group(choices.pdfa ? "Standard (embedded, PDF/A)" : "Standard", choices.standard);
    });

    const changes = () => {
      const out = {};
      if (now.font) out.font = now.font;
      if (Math.abs(now.size - start.size) >= 0.25) out.size = now.size;
      if (now.bold !== start.bold) out.bold = now.bold;
      if (now.italic !== start.italic) out.italic = now.italic;
      if (now.color !== start.color) out.color = Reader.#rgb01(now.color);
      if (now.align) out.align = now.align;
      return out;
    };
    return { choices: changes, changed: () => Object.keys(changes()).length > 0 };
  }

  /** The fonts to offer (see /api/text/fonts), asked once per version of the document. */
  async #fontChoices() {
    const tab = this.activeTab;
    if (!tab) return null;
    if (tab.fontChoices?.version !== tab.version) {
      const ask = async () => {
        try {
          const bytes = tab.bytes || await this.#currentBytes();
          const res = await this.#post("/api/text/fonts", bytes, tab);
          return res.ok ? await res.json() : null;
        } catch (e) {
          return null;  // the editor works without the list: the font as is, size, colour
        }
      };
      tab.fontChoices = { version: tab.version, promise: ask() };
    }
    return tab.fontChoices.promise;
  }

  /** Notes on a change: the font it got when one was picked, a smaller
   *  size, words that ran on into the next column: [sentence]. */
  #fontNotes(used) {
    const notes = [];
    for (const u of used) {
      if (u.asked && u.matched === false) {
        notes.push(u.reason === "pdfa"
          ? `This is a PDF/A file, which needs every font inside it; ${u.asked} isn't, so ${u.font} was used.`
          : `${u.asked} can't show those letters in that style here, so ${u.font} was used.`);
      }
      if (u.size) notes.push(`The text was made a little smaller (${u.size} pt) to fit.`);
      if (u.linked) notes.push("What didn't fit in its column runs on at the top of the next one.");
    }
    return notes;
  }

  /** The last change again, its text made a little smaller to fit the page. */
  async #shrinkToFit(edit) {
    await this.#undoTextEdit();
    return this.#applyTextEdit({ ...edit, fit: "shrink" });
  }

  /** "ABCDEF+TimesNewRomanPS-BoldMT" → "Times New Roman". */
  static #familyOf(name) {
    let family = String(name || "").replace(/^[A-Z]{6}\+/, "").split(/[-,]/)[0];
    family = family.replace(/(PS)?MT$|PS$/, "").replace(/([a-z])([A-Z])/g, "$1 $2");
    return family.replace(/\s+(Bold|Italic|Oblique|Regular|BoldItalic)$/i, "").trim();
  }

  static #familyKey(name) { return String(name || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }

  /** A CSS font-family for a family picked in the list. */
  static #cssFamily(name, generic) {
    const standard = { helvetica: "Helvetica, Arial, sans-serif", times: '"Times New Roman", Times, serif',
      courier: '"Courier New", Courier, monospace' }[Reader.#familyKey(name)];
    return standard || `"${name.replace(/["\\]/g, "")}", ${generic}`;
  }

  static #hexColour(css) {
    const m = String(css).match(/\d+(\.\d+)?/g);
    if (/^#[0-9a-f]{6}$/i.test(css)) return css.toLowerCase();
    if (!m || m.length < 3) return "#000000";
    return "#" + m.slice(0, 3).map((v) => Math.max(0, Math.min(255, Math.round(+v))).toString(16).padStart(2, "0")).join("");
  }

  static #rgb01(hex) {
    return [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) / 255 * 1000) / 1000);
  }
  // -- end of text font picker and reflow --

  /** The view (page, zoom, scroll), to come back to after reloading. */
  #view() {
    return { page: this.pdfViewer.currentPageNumber, scale: this.pdfViewer.currentScaleValue,
      top: this.container.scrollTop, left: this.container.scrollLeft };
  }

  #post(url, bytes, tab, fields = {}, files = []) {
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
  #changeDocument(make, { failure = "Could not change the document: ", goto = null, after = null, signing = false } = {}) {
    return this.#serial(async () => {
      const tab = this.activeTab;
      if (!tab || !this.doc) return;
      // Changes are made by rewriting the file, which a signature can't survive.
      if (!signing && tab.signatures?.length && !tab.signedWarned) {
        if (!confirm("This document is signed. Changing it makes its signatures show as broken: they no longer cover what the document says. Change it anyway?")) return;
        tab.signedWarned = true;
      }
      this.#busy(true);
      try {
        const bytes = await this.#currentBytes();
        const res = await make(bytes, tab);
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
        const changed = new Uint8Array(await res.arrayBuffer());
        if (!this.tabs.includes(tab)) return;
        (tab.undo ||= []).push(bytes);
        if (tab.undo.length > 30) tab.undo.shift();
        Object.assign(tab, { bytes: changed, afterEdit: changed, edited: true, dirty: true,
          version: (tab.version || 0) + 1 });
        if (tab === this.activeTab) { tab.goto = goto ? goto(this.#view()) : this.#view(); await this.#reloadEditing(tab); }
        else this.#renderTabs();
        after?.(res);
      } catch (e) {
        this.toast(failure + (e.message || e));
      } finally {
        this.#busy(false);
      }
    });
  }

  #applyTextEdit(edit) {
    return this.#changeDocument((bytes, tab) => this.#post("/api/text/edit", bytes, tab, { edits: JSON.stringify([edit]) }), {
      failure: "Could not change the text: ",
      after: (res) => {
        // Say when the words couldn't get the document's own font.
        let used = [];
        try { used = JSON.parse(res.headers.get("X-Pdfm-Fonts") || "[]"); } catch (e) {}
        const other = used.find((u) => u.kind !== "same" && !u.asked);
        const notes = this.#fontNotes(used);
        if (other) {
          notes.push(other.kind === "installed"
            ? `This PDF's copy of the font lacks some of those letters, so ${other.font} from this computer was used.`
            : `This PDF's copy of the font lacks some of those letters, so a similar font (${other.font}) was used.`);
        }
        if (used.some((u) => u.wider)) notes.push("The line is now longer than its column: to wrap it again, rewrite the whole paragraph (Shift+click it).");
        if (used.some((u) => u.taller)) notes.push("The paragraph now takes more lines than before, so it may run into what's below it. Undo with Ctrl+Z.");
        if (used.some((u) => u.overflow) && !edit.fit) {
          notes.push("The text no longer fits on the page, so the text below it wasn't moved. Shorten it, or let it shrink a little.");
          return this.toast(notes.join(" "), 12000, ["Shrink to fit", () => this.#shrinkToFit(edit)]);
        }
        if (used.some((u) => u.overflow)) notes.push("Even a little smaller, the text doesn't fit on the page, so the text below it wasn't moved.");
        if (notes.length) this.toast(notes.join(" "), 8000);
      },
    });
  }

  #undoTextEdit() {
    return this.#serial(async () => {
      const tab = this.activeTab;
      if (!tab?.undo?.length) return;
      // Undoing goes back to the bytes before the change; anything added
      // since (drawings, form entries) would go with it.
      if ((this.#changed() || tab.bytes !== tab.afterEdit)
          && !confirm("Undoing the change also takes away the annotations and form entries made since it. Continue?")) return;
      const previous = tab.undo.pop();
      const back = previous === tab.original;
      Object.assign(tab, { bytes: previous, afterEdit: previous, edited: !back, dirty: !back, goto: this.#view(),
        version: (tab.version || 0) + 1 });
      await this.#reloadEditing(tab);
      this.toast("Change undone", 1500);
    });
  }

  /** Show the tab's changed bytes, staying in the Edit text tool if it was on. */
  async #reloadEditing(tab) {
    const editing = this.textEditMode ? "EDITTEXT" : this.markupMode, picked = this.pickedImage;
    await this.#load(tab);
    this.#renderTabs();
    if (tab !== this.activeTab) return;
    if (editing) {
      this.openAnnotate();
      this.#setEditMode(editing);
      if (editing === "IMAGES" && picked) this.pickedImage = picked;  // the picture just moved stays picked
    }
    this.undoBtn.disabled = !tab.undo?.length;
  }

  // ---- saving, printing, editing ------------------------------------------------
  /** Filled-in form fields or annotations that aren't in the file yet. */
  #changed() { return !!(this.doc && this.doc.annotationStorage.size > 0); }
  #edited() { return this.#changed() || !!this.activeTab?.edited; }

  async #currentBytes() {
    this.#finishEditing();
    if (!this.#changed()) return await this.#fullBytes();
    return await this.doc.saveDocument();
  }

  /** The shown document's bytes; a file read in pieces is read in full now. */
  async #fullBytes() {
    if (!this.bytes && this.doc) {
      const tab = this.activeTab, data = await this.doc.getData();
      this.bytes = data;
      if (tab && !tab.bytes) tab.bytes = tab.original = data;
    }
    return this.bytes;
  }

  #copyName() {
    const stem = this.name.replace(/\.pdf$/i, "");
    return this.#edited() ? `${stem} (edited).pdf` : `${stem}.pdf`;
  }

  /** Save the changes into the file itself when it was opened from this
   *  computer (Ctrl+S); otherwise as a copy. */
  async save() {
    await this.chain;  // a text change still on its way is included
    const tab = this.activeTab;
    if (!this.doc || !tab) return;
    if (!tab.fileId) { await this.saveCopy(); return; }
    if (!tab.dirty && !this.#changed()) { this.toast("No changes to save", 1500); return; }
    const version = tab.version || 0;
    const bytes = await this.#currentBytes();
    const put = (force) => fetch("/api/save/" + encodeURIComponent(tab.fileId), { method: "POST", body: bytes,
      headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/pdf", "X-Pdfm-Stamp": tab.stamp || "",
        ...(force ? { "X-Pdfm-Force": "1" } : {}) } });
    try {
      let res = await put(false);
      if (res.status === 409) {
        if (!confirm(`“${tab.name}” was changed by another program since you opened it.\n\nReplace it with your version? (Cancel saves your version as a copy instead.)`)) {
          await this.saveCopy();
          return;
        }
        res = await put(true);
      }
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error || res.statusText);
      // The file now holds these bytes (and a file read in pieces is in memory now).
      Object.assign(tab, { stamp: answer.stamp, original: bytes, size: bytes.length });
      if (!tab.bytes || tab === this.activeTab && !this.bytes) tab.bytes = bytes;
      this.#markSaved(tab, version);
      this.toast(`Saved ${tab.name}`, 2000);
    } catch (e) {
      this.toast("Could not save: " + (e.message || e), 9000, ["Save as…", () => this.saveCopy()]);
    }
  }

  /** Save as a new file, starting in the document's own folder (Ctrl+Shift+S). */
  async saveCopy() {
    await this.chain;  // a text change still on its way is included
    if (!this.doc) return;
    const tab = this.activeTab, version = tab.version || 0;
    const bytes = await this.#currentBytes();
    await this.#saveBytes(bytes, this.#copyName(), () => this.#markSaved(tab, version), tab?.fileId);
  }

  /** Save ``bytes`` as a file the person picks (or download it); the
   *  dialog starts next to the opened file ``near`` (its id). */
  async #saveBytes(bytes, name, saved = () => {}, near = null) {
    const api = this.api();
    if (this.isDesktop() && api && api.save_result) {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), name);
      try {
        const res = await fetch("/api/keep", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
        const { id } = await res.json();
        const path = await api.save_result(id, name, near);
        if (path) { saved(); this.toast("Saved to " + path, 5000, ["Show in folder", () => api.reveal(path)]); }
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
    const pages = thumb && this.selected.has(page) ? [...this.selected].sort((a, b) => a - b) : [page];
    if (thumb && !this.selected.has(page)) this.#select([page]);
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
        items.push(item("edittext", "Edit this text", () => { this.useTool("EDITTEXT"); this.#openTextEditor(span, pageEl, page, "line"); }));
        items.push(item("edittext", "Rewrite this paragraph", () => { this.useTool("EDITTEXT"); this.#openTextEditor(span, pageEl, page, "paragraph"); }));
      }
      const at = { clientX: e.clientX, clientY: e.clientY };
      items.push(item("text", "Add text here", () => { this.useTool("EDITTEXT"); this.#openNewText(at, pageEl, page); }));
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
  #pagePlan() { return Array.from({ length: this.doc.numPages }, (_, i) => ({ page: i + 1 })); }
  static #pagesText(pages) { return pages.length === 1 ? `Page ${pages[0]}` : `${pages.length} pages`; }

  #arrange(plan, { inserts = [], message = "", page = null } = {}) {
    return this.#changeDocument((bytes, tab) => this.#post("/api/pages/arrange", bytes, tab, { plan: JSON.stringify(plan) }, inserts), {
      failure: "Could not change the pages: ",
      goto: (view) => ({ page: page || view.page, scale: view.scale }),
      after: () => { if (message) this.toast(message, 5000, ["Undo", () => this.#undoTextEdit()]); },
    });
  }
  rotatePages(pages, delta) {
    if (!this.doc || !pages.length) return;
    const plan = this.#pagePlan().map((e) => (pages.includes(e.page) ? { ...e, rotate: delta } : e));
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
      const bytes = await this.#currentBytes();
      const plan = [...pages].sort((a, b) => a - b).map((page) => ({ page }));
      const res = await this.#post("/api/pages/arrange", bytes, tab, { plan: JSON.stringify(plan) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const out = new Uint8Array(await res.arrayBuffer());
      const stem = this.name.replace(/\.pdf$/i, "");
      const label = pages.length === 1 ? `page ${pages[0]}` : `${pages.length} pages`;
      this.#busy(false);
      await this.#saveBytes(out, `${stem} (${label}).pdf`);
    } catch (e) {
      this.toast("Could not extract the pages: " + (e.message || e));
    } finally {
      this.#busy(false);
    }
  }

  /** Commit the annotation being drawn or typed. */
  #finishEditing() {
    try { this.uiManager?.endCurrentEditing?.(); } catch (e) { /* nothing being edited */ }
  }

  /** The tab's changes up to ``version`` are in a file now. */
  /** Save and Undo in the toolbar while there are changes to keep or take back. */
  #showSaveState() {
    if (!this.saveNowBtn) return;
    // (The annotation bar has its own while it's open.)
    const tab = this.activeTab, open = !!(this.doc && tab && this.annotbar.hidden);
    this.saveNowBtn.hidden = !(open && tab.dirty);
    this.undoNowBtn.hidden = !(open && (this.annotUndo || tab.undo?.length));
  }

  /** Take back the last change: an annotation if one was just made, else a page or text change. */
  #undoLast() {
    if (this.annotUndo && this.uiManager) this.uiManager.undo();
    else if (this.activeTab?.undo?.length) this.#undoTextEdit();
  }

  #markSaved(tab, version) {
    if (!tab || !this.tabs.includes(tab) || (tab.version || 0) !== version) return;  // changed since
    tab.dirty = false;
    this.#renderTabs();
  }

  async print() {
    if (!this.doc) return;
    const doc = this.doc, total = doc.numPages;
    if (total > 300 && !confirm(`Prepare all ${total} pages for printing? This can take a while.`)) return;
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
    try {
      for (let n = 1; n <= total; n++) {
        this.toast(`Preparing page ${n} of ${total} for printing…`, 60000);
        const page = await doc.getPage(n);
        const viewport = page.getViewport({ scale: 150 / 72 });  // 150 dpi
        const canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
        await page.render({ canvas, viewport, intent: "print", annotationMode: pdfjs.AnnotationMode.ENABLE_STORAGE,
          printAnnotationStorage: doc.annotationStorage.print }).promise;
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
    this.#setEditMode(name, name === "STAMP");
  }
  editWith(toolId) { return this.#edit(toolId); }
  goToPage(n) { if (this.doc && n >= 1 && n <= this.pdfViewer.pagesCount) this.pdfViewer.currentPageNumber = n; }
  showBookmarks() { this.#setSidebar(true); this.#showTab("outline"); }

  // ---- drawing scale and measuring ---------------------------------------------
  // For to-scale drawings such as house plans: each sheet's scale (read from
  // the sheet, or set by hand), zoom to true paper size or to a drawing
  // scale on a calibrated screen, and a tool to measure lengths and areas.

  #buildScaleBar() {
    const option = (value, label) => el("option", { value }, label);
    const group = (label, scales) => el("optgroup", { label }, ...scales.map((sc) => option("s:" + measure.ALL_SCALES.indexOf(sc), sc.label)));
    this.sheetScaleSelect = el("select", { class: "rd-zoom rd-scale-select", "aria-label": "Drawing scale of this sheet" });
    this.sheetDetected = option("detected", "");
    this.sheetCustom = option("custom", "");
    this.sheetScaleSelect.append(option("", "Not set"), this.sheetDetected, this.sheetCustom,
      group("Architectural", measure.ARCHITECTURAL), group("Engineering", measure.ENGINEERING),
      group("Metric", measure.METRIC), option("full", measure.FULL_SIZE.label));
    this.fromDimensionBtn = el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": "false",
      title: "Click both ends of a line whose real length you know, such as a dimension", html: svg("ruler") + "<span>From a dimension</span>" });
    this.viewAtSelect = el("select", { class: "rd-zoom", "aria-label": "Zoom to scale",
      title: "Zoom so the drawing is its true paper size on your screen, or looks as if printed at another scale" });
    this.distanceBtn = el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": "false",
      title: "Measure a length: click both ends (M). Clicks snap to the drawing's lines (Alt: no snapping); hold Shift to keep it straight.", html: svg("ruler") + "<span>Distance</span>" });
    this.pathBtn = el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": "false",
      title: "Measure a run of lines (walls, a baseboard): click along it, then Enter or double-click for the total", html: svg("path") + "<span>Path</span>" });
    this.areaBtn = el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": "false",
      title: "Measure an area: click its corners, then click the first one again (or press Enter)", html: svg("area") + "<span>Area</span>" });
    const tool = (mode, icon, label, title) => el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": "false", "data-measure": mode, title, html: svg(icon) + `<span>${label}</span>` });
    this.rectBtn = tool("rect", "rect", "Rectangle", "Measure a rectangular area: click two opposite corners");
    this.cutoutBtn = tool("cutout", "cutout", "Cut-out", "Take an opening (a stair, a shaft, a column) out of an area: click its corners inside the area, then the first one again (or Enter)");
    this.countBtn = tool("count", "count", "Count", "Count things (outlets, doors, fixtures): click each one. Enter starts a new count.");
    this.angleBtn = tool("angle", "angle", "Angle", "Measure an angle: click a point on one line, the corner, then a point on the other line");
    this.clearMeasuresBtn = el("button", { class: "rd-btn", type: "button", title: "Remove every measurement", "aria-label": "Remove every measurement", html: svg("trash") });
    this.straightOnly = !!store.get("pdfm-measure-straight", false);
    this.straightBtn = el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": String(this.straightOnly),
      title: "Straight only: lines go across or up and down, never at a slant (Shift: steps of 45°)", html: svg("straight") + "<span>Straight</span>" });
    this.units = store.get("pdfm-measure-units", "auto");
    this.listBtn = el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": "false",
      title: "The list of measurements: values, totals, units, copy", html: svg("list") + "<span>List</span>" });
    this.listCount = el("b", { class: "rd-count", hidden: true });
    this.listBtn.append(this.listCount);
    this.measPanel = el("aside", { class: "rd-measpanel", hidden: true, "aria-label": "Measurements" });
    this.calibrateBtn = el("button", { class: "rd-btn", type: "button", title: "Calibrate the screen: match the app to it, so true paper size really is",
      "aria-label": "Calibrate the screen", html: svg("screen") });
    this.compareBtn = el("button", { class: "rd-btn", type: "button", title: "Compare with another revision of the drawing: changes show in red and blue",
      "aria-label": "Compare with another revision", html: svg("layers") });
    this.compareBtn.addEventListener("click", () => (this.overlay ? this.closeCompare() : this.compareRevision()));
    const close = el("button", { class: "rd-btn", type: "button", title: "Close the scale bar", "aria-label": "Close the scale bar", html: svg("x") });
    this.scalebar = el("div", { class: "rd-findbar rd-scalebar", hidden: true, role: "toolbar", "aria-label": "Scale and measuring" },
      el("label", {}, "Sheet scale", this.sheetScaleSelect), this.fromDimensionBtn, el("span", { class: "rd-sep" }),
      el("label", {}, "Zoom to scale", this.viewAtSelect), el("span", { class: "rd-sep" }),
      this.distanceBtn, this.pathBtn, this.areaBtn, this.rectBtn, this.cutoutBtn, this.countBtn, this.angleBtn, this.straightBtn,
      el("span", { class: "rd-sep" }), this.listBtn, this.clearMeasuresBtn,
      el("span", { style: "flex:1" }), this.compareBtn, this.calibrateBtn, close);
    this.sheetScaleSelect.addEventListener("change", () => this.#pickSheetScale(this.sheetScaleSelect.value));
    this.fromDimensionBtn.addEventListener("click", () => this.measureTool(this.measureMode === "calibrate" ? null : "calibrate"));
    this.viewAtSelect.addEventListener("change", () => { if (this.viewAtSelect.value) this.#viewAt(this.viewAtSelect.value); });
    this.distanceBtn.addEventListener("click", () => this.measureTool(this.measureMode === "distance" ? null : "distance"));
    this.areaBtn.addEventListener("click", () => this.measureTool(this.measureMode === "area" ? null : "area"));
    for (const btn of [this.rectBtn, this.cutoutBtn, this.countBtn, this.angleBtn]) {
      btn.addEventListener("click", () => {
        if (btn.dataset.measure === "count" && this.measureMode === "count" && this.countTarget) {  // again: a new count
          this.countTarget = null;
          this.toast("Next clicks start a new count", 1500);
          return;
        }
        this.measureTool(this.measureMode === btn.dataset.measure ? null : btn.dataset.measure);
      });
    }
    this.pathBtn.addEventListener("click", () => {
      if (this.measureMode === "path" && this.draft?.pts.length > 1) { this.#finishMeasure(); return; }  // the button again: done
      this.measureTool(this.measureMode === "path" ? null : "path");
    });
    this.clearMeasuresBtn.addEventListener("click", () => this.clearMeasures());
    this.straightBtn.addEventListener("click", () => {
      this.straightOnly = !this.straightOnly;
      store.set("pdfm-measure-straight", this.straightOnly);
      this.straightBtn.setAttribute("aria-pressed", String(this.straightOnly));
    });
    this.listBtn.addEventListener("click", () => this.#toggleMeasPanel());
    this.calibrateBtn.addEventListener("click", () => this.calibrateScreen());
    close.addEventListener("click", () => this.toggleScaleBar(false));
  }

  toggleScaleBar(open = this.scalebar.hidden) {
    if (open && !this.doc) return;
    this.scalebar.hidden = !open;
    this.scaleBtn.setAttribute("aria-expanded", String(open));
    if (open) this.#refreshScale();
    else this.measureTool(null);
  }

  /** The scale of page ``n``: set by hand for that page, else read from the
   *  sheet, else the one last set by hand in this document. null if none. */
  #pageScale(n) {
    const tab = this.activeTab;
    if (!tab) return null;
    const own = tab.scales?.[n];
    if (own === false) return null;
    return own || tab.detected?.get(n) || tab.scales?.["*"] || null;
  }

  #scaleKey() { return "pdfm-scale:" + this.#key(); }

  /** Read the scale written on the current sheet (once per page), then show it. */
  async #refreshScale() {
    const tab = this.activeTab, doc = this.doc;
    if (!tab || !doc) return;
    if (!tab.scales) {
      const saved = store.get(this.#scaleKey(), {});
      tab.scales = {};
      if (saved && typeof saved === "object") for (const [k, v] of Object.entries(saved)) if (v === false || isScale(v)) tab.scales[k] = v;
    }
    tab.detected ||= new Map();
    tab.stored ||= this.#storedScales(tab);
    const n = this.pdfViewer.currentPageNumber || 1;
    if (!tab.detected.has(n)) {
      tab.detected.set(n, null);
      // A scale the drawing program stored in the PDF is exact; the one
      // written on the sheet is read from its text.
      const stored = (await tab.stored)[n]?.[0];
      if (doc !== this.doc) return;
      if (stored) {
        const near = measure.nearestScale(stored.ratio, stored.units);
        const label = stored.label || (near.off < 0.005 ? near.scale.label : `1:${+stored.ratio.toFixed(stored.ratio < 10 ? 2 : 0)}`);
        tab.detected.set(n, { ratio: stored.ratio, units: stored.units, label, stored: true });
      }
      else try {
        const page = await doc.getPage(n);
        const content = await page.getTextContent();
        const text = content.items.map((i) => (i.str || "") + (i.hasEOL ? "\n" : " ")).join("");
        tab.detected.set(n, measure.findScale(text));
      } catch (e) { /* no text: no scale */ }
      if (doc !== this.doc) return;
    }
    this.#showScale();
  }

  /** {page: [viewport scale, biggest first]} stored in the PDF (see viewports.py). */
  async #storedScales(tab) {
    try {
      const body = new FormData();
      if (tab.fileId && !tab.edited) body.append("id", tab.fileId);
      else {
        const bytes = tab === this.activeTab ? await this.#fullBytes() : tab.bytes;  // (annotations don't matter here)
        if (!bytes) return {};
        body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      }
      if (tab.password) body.append("password", tab.password);
      const res = await fetch("/api/scales", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
      return res.ok ? (await res.json()).pages || {} : {};
    } catch (e) {
      return {};
    }
  }

  #showScale() {
    if (!this.doc) return;
    const n = this.pdfViewer.currentPageNumber || 1;
    const scale = this.#pageScale(n);
    const own = this.activeTab?.scales?.[n];
    const detected = this.activeTab?.detected?.get(n);
    this.scaleLabel.textContent = scale ? (scale.custom ? "≈ " + scale.label.replace(/^.*\((.*)\)$/, "$1") : scale.label) : "Scale";
    this.scaleBtn.classList.toggle("set", !!scale);
    this.sheetDetected.hidden = !detected;
    this.sheetDetected.textContent = detected ? `${detected.label} (${detected.stored ? "stored in the PDF" : "from the sheet"})` : "";
    const custom = scale?.custom ? scale : null;
    this.sheetCustom.hidden = !custom;
    this.sheetCustom.textContent = custom ? custom.label : "";
    let value = "";
    if (scale && scale === detected && !own) value = "detected";
    else if (custom) value = "custom";
    else if (scale && scale.ratio === 1) value = "full";
    else if (scale) {
      const i = measure.ALL_SCALES.findIndex((sc) => sc.label === scale.label && sc.ratio === scale.ratio);
      value = i >= 0 ? "s:" + i : "";
    }
    this.sheetScaleSelect.value = value;
    // "Show at": true paper size, or a drawing scale of the same kind as this sheet's.
    const options = [el("option", { value: "" }, "Not to scale"), el("option", { value: "true" }, "True paper size")];
    if (scale) {
      const kind = (sc) => (sc.units === "m" ? "m" : sc.label.includes("= 1'-0") ? "arch" : "eng");
      const same = measure.ALL_SCALES.filter((sc) => (scale.custom ? sc.units === scale.units : kind(sc) === kind(scale)));
      options.push(el("optgroup", { label: "As if printed at" }, ...same.map((sc) => el("option", { value: "s:" + measure.ALL_SCALES.indexOf(sc) }, sc.label))));
    }
    this.viewAtSelect.replaceChildren(...options);
    this.#syncViewAt();
    this.#drawAllMeasures();
  }

  #pickSheetScale(value) {
    const tab = this.activeTab;
    if (!tab || value === "custom") return;
    const n = this.pdfViewer.currentPageNumber || 1;
    tab.scales ||= {};
    if (value === "detected") delete tab.scales[n];
    else if (value === "") tab.scales[n] = false;
    else {
      const scale = value === "full" ? measure.FULL_SIZE : measure.ALL_SCALES[+value.slice(2)];
      tab.scales[n] = scale;
      tab.scales["*"] = scale;  // and for pages that show no scale of their own
    }
    store.set(this.#scaleKey(), tab.scales);
    this.#showScale();
  }

  #setPageScale(n, scale) {
    const tab = this.activeTab;
    tab.scales ||= {};
    tab.scales[n] = scale;
    tab.scales["*"] = scale;
    store.set(this.#scaleKey(), tab.scales);
    this.#showScale();
  }

  /** The zoom at which a page shows at its true printed size on this screen. */
  #trueZoom() {
    const devicePpi = this.#screenPpi();
    const cssPpi = devicePpi ? devicePpi / (window.devicePixelRatio || 1) : 96;
    return cssPpi / 96;  // at 100%, pdf.js draws 96 CSS pixels to the inch
  }

  /** The screen's pixels per inch, as calibrated (kept here as well as in
   *  storage, which may be unavailable); null if not yet, or nonsense. */
  #screenPpi() {
    const ppi = this.screenPpi ?? store.get("pdfm-screen-ppi", null);
    return Number.isFinite(ppi) && ppi >= 30 && ppi <= 1000 ? ppi : null;
  }

  /** Zoom to true paper size ("true") or so the drawing looks as if drawn at
   *  another scale ("s:<index>"). */
  async #viewAt(value) {
    if (!this.doc) return;
    if (!this.#screenPpi() && !(await this.calibrateScreen(true))) { this.#syncZoomSelect(); this.#syncViewAt(); return; }
    let zoom = this.#trueZoom(), what = "true paper size";
    if (value !== "true") {
      const target = measure.ALL_SCALES[+value.slice(2)];
      const scale = this.#pageScale(this.pdfViewer.currentPageNumber || 1);
      if (!target || !scale) { this.toast("Set this sheet's scale first."); return; }
      zoom *= scale.ratio / target.ratio;
      what = target.label;
    }
    const wanted = zoom;
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
    if (!Number.isFinite(zoom)) { this.toast("That zoom can't be worked out; calibrate the screen again."); return; }
    this.pdfViewer.currentScale = zoom;
    if (zoom !== wanted) this.toast(`That would be ${Math.round(wanted * 100)}%; the viewer stops at ${Math.round(zoom * 100)}%.`, 4000);
    this.#syncViewAt();
    this.toast(`Showing the drawing at ${what} on your screen`, 2500);
  }

  /** Zoom to scale shows what the zoom is now: true paper size, as if
   *  printed at a scale, or not to scale (and the zoom). */
  #syncViewAt() {
    const sel = this.viewAtSelect;
    if (!sel?.options.length || !this.pdfViewer || !this.doc) return;
    const zoom = this.pdfViewer.currentScale;
    let value = "";
    if (this.#screenPpi() && Number.isFinite(zoom)) {
      const t = this.#trueZoom(), same = (z) => Math.abs(zoom / z - 1) < 0.002;
      const scale = this.#pageScale(this.pdfViewer.currentPageNumber || 1);
      if (same(t)) value = "true";
      else if (scale) {
        for (const o of sel.options) {
          const target = o.value.startsWith("s:") ? measure.ALL_SCALES[+o.value.slice(2)] : null;
          if (target && same(t * scale.ratio / target.ratio)) { value = o.value; break; }
        }
      }
    }
    sel.options[0].textContent = value || !Number.isFinite(zoom) ? "Not to scale" : `Not to scale (${Math.round(zoom * 100)}%)`;
    sel.value = value;
  }

  #syncZoomSelect() {
    if (this.pdfViewer) this.eventBus.dispatch("scalechanging", { source: this, scale: this.pdfViewer.currentScale, presetValue: this.pdfViewer.currentScaleValue });
  }

  /** Ask how big the screen's pixels really are: match a bank card, or give
   *  the screen's diagonal. Resolves true once calibrated. */
  async calibrateScreen(first = false) {
    const dpr = window.devicePixelRatio || 1;
    let cssPpi = (this.#screenPpi() || 96 * dpr) / dpr;
    const CARD_W = 3.370, CARD_H = 2.125;  // a bank card, in inches (85.6 × 54 mm)
    const card = el("div", { class: "rd-card" }, el("span", {}, "Bank card"));
    const ruler = el("div", { class: "rd-ruler", "aria-hidden": "true" });
    let rulerUnit = store.get("pdfm-ruler-unit", "in") === "cm" ? "cm" : "in";
    const marks = () => {
      const count = rulerUnit === "cm" ? 15 : 6;
      ruler.replaceChildren(...Array.from({ length: count + 1 }, (_, i) => el("i", { style: `left:${(i / count) * 100}%` }, el("b", {}, String(i)))),
        el("span", { style: `left:${50 / count}%` }, rulerUnit === "cm" ? "15 cm" : "6 inches"));
    };
    const unitBtn = (unit, label) => {
      const b = el("button", { type: "button", class: "rd-segbtn", "aria-pressed": String(rulerUnit === unit) }, label);
      b.addEventListener("click", () => {
        rulerUnit = unit;
        store.set("pdfm-ruler-unit", unit);
        for (const other of b.parentElement.children) other.setAttribute("aria-pressed", String(other === b));
        marks();
        show();
      });
      return b;
    };
    const units = el("div", { class: "rd-seg", role: "group", "aria-label": "Ruler" }, unitBtn("in", "Inches"), unitBtn("cm", "Centimetres"));
    marks();
    const slider = el("input", { type: "range", min: "40", max: "320", step: "0.25", "aria-label": "Card size" });
    const diagonal = el("input", { type: "number", min: "5", max: "100", step: "0.1", placeholder: "e.g. 24", "aria-label": "Screen diagonal in inches" });
    const show = () => {
      card.style.width = `${CARD_W * cssPpi}px`;
      card.style.height = `${CARD_H * cssPpi}px`;
      ruler.style.width = `${(rulerUnit === "cm" ? 15 / 2.54 : 6) * cssPpi}px`;
      slider.value = String(cssPpi);
    };
    slider.addEventListener("input", () => { cssPpi = +slider.value; diagonal.value = ""; show(); });
    diagonal.addEventListener("input", () => {
      const d = +diagonal.value, w = screen?.width, h = screen?.height;
      if (d >= 5 && w > 0 && h > 0) { cssPpi = Math.min(320, Math.max(40, Math.hypot(w, h) / d)); show(); }
    });
    show();
    const body = [
      el("p", {}, first ? "To show drawings at their real size, ZPDF needs to know how big your screen's pixels are. This is done once."
        : "Make true paper size match the real world on this screen."),
      el("p", {}, "Hold a ruler or a bank card against the screen and drag the slider until the ruler's marks, or the card's outline, match exactly (a ruler is the more exact):"),
      units, el("div", { class: "rd-cardbox" }, ruler, card), slider,
      el("p", { class: "rd-or" }, "Or type your screen's size (its diagonal, as sold):"),
      el("label", { class: "rd-diag" }, diagonal, "inches"),
    ];
    const answer = await this.#dialog("Calibrate your screen", body, [["cancel", "Cancel"], ["ok", "Save", true]]);
    if (answer !== "ok" || !Number.isFinite(cssPpi)) return false;
    this.screenPpi = cssPpi * dpr;
    store.set("pdfm-screen-ppi", this.screenPpi);
    this.toast("Screen calibrated: true paper size now matches the real world.", 3000);
    if (this.zoomPreset === "true-size" && this.pdfViewer) this.pdfViewer.currentScale = this.#trueZoom();
    return true;
  }

  /** Start measuring ("distance" or "area"), set the scale from a line of
   *  known length ("calibrate"), or stop (null). */
  measureTool(mode) {
    if (mode && !this.doc) return;
    if (!mode && !this.measureMode) return;
    if (mode) {
      if (this.textEditMode || (this.editMode && this.editMode !== "NONE")) this.#setEditMode("NONE");
      this.closeAnnotate();
      if (this.scalebar.hidden) this.toggleScaleBar(true);
      if (this.container.classList.contains("hand")) this.toggleHand();
    }
    const page = this.draft?.page, aimPage = this.aim?.page;
    this.measureMode = mode;
    this.draft = null;
    this.aim = null;
    this.pick = null;
    this.pendingDrag = null;
    if (this.dragging) { this.dragging = null; this.container.classList.remove("rd-dragging"); }
    this.container.style.cursor = "";
    if (page) this.#drawMeasures(page);
    if (aimPage && aimPage !== page) this.#drawMeasures(aimPage);
    if (mode) this.#geometry(this.pdfViewer.currentPageNumber || 1);  // start reading the lines now
    this.root.classList.toggle("rd-measuring", !!mode);
    this.distanceBtn.setAttribute("aria-pressed", String(mode === "distance"));
    this.areaBtn.setAttribute("aria-pressed", String(mode === "area"));
    this.pathBtn.setAttribute("aria-pressed", String(mode === "path"));
    for (const btn of [this.rectBtn, this.cutoutBtn, this.countBtn, this.angleBtn]) btn.setAttribute("aria-pressed", String(mode === btn.dataset.measure));
    this.countTarget = null;
    if (!mode) this.selected = null;
    this.fromDimensionBtn.setAttribute("aria-pressed", String(mode === "calibrate"));
    if (mode === "path") this.toast("Click along the run of lines; Enter, a double-click or Path again for the total. Backspace takes back a point.", 5000);
    else if (mode === "count") this.toast("Click each thing to count it; each gets a number. Enter (or Count again) starts a new count, e.g. for another kind of fixture.", 6000);
    else if (mode === "cutout") this.toast("Click the corners of the opening inside an area, then the first corner again (or Enter): it's taken off that area.", 6000);
    else if (mode === "angle") this.toast("Click a point on one line, then the corner, then a point on the other line.", 5000);
    else if (mode === "calibrate") this.toast("Click both ends of a line whose real length you know, such as a dimension's extension lines. Clicks snap to the drawing's lines.", 6000);
    else if (mode && !this.#pageScale(this.pdfViewer.currentPageNumber || 1)) {
      this.toast("This sheet's scale isn't set, so lengths show as on paper. Pick it under Sheet scale, or use Set from a dimension.", 6000);
    } else if (mode && !this.activeTab.scaleChecked) {
      const n = this.pdfViewer.currentPageNumber || 1;
      if (this.#pageScale(n) === this.activeTab.detected?.get(n) && !this.#pageScale(n).stored) {
        this.activeTab.scaleChecked = true;
        this.toast(`The scale ${this.#pageScale(n).label} was read from the sheet. Measure one written dimension to check it before relying on it.`, 7000);
      }
    }
  }

  clearMeasures() {
    if (this.activeTab?.measures?.length) this.#rememberMeasures();
    if (this.activeTab) this.activeTab.measures = [];
    this.selected = null;
    this.draft = null;
    this.#drawAllMeasures();
  }

  /** The lines of page ``n`` (see snap.js), read from the file the first
   *  time they're needed; null while they're being read. */
  #geometry(n) {
    const tab = this.activeTab, doc = this.doc;
    if (!tab || !doc) return null;
    tab.geometry ||= new Map();
    const have = tab.geometry.get(n);
    if (have !== undefined) {
      if (have) { tab.geometry.delete(n); tab.geometry.set(n, have); }  // most recently used, kept longest
      return have || null;
    }
    tab.geometry.set(n, false);  // being read
    const done = (geometry, why) => {
      if (doc !== this.doc) return;
      tab.geometry.set(n, geometry);
      // Lines of pages long left are let go of; the ones being read stay.
      for (const [page, g] of tab.geometry) {
        if (tab.geometry.size <= GEOMETRY_PAGES) break;
        if (g && page !== n) tab.geometry.delete(page);
      }
      if (this.measureMode && this.pdfViewer.currentPageNumber === n && !tab.noLinesHinted) {
        let hint = null;
        if (why) hint = "This sheet's lines couldn't be read, so points go exactly where you click: zoom in close.";
        else if (!geometry.count) hint = "This sheet has no line work to snap to (a scan?): points go exactly where you click, so zoom in close.";
        else if (geometry.truncated) hint = "This sheet has more lines than can be read; snapping covers the first part of it.";
        if (hint) { tab.noLinesHinted = true; this.toast(hint, 6000); }
      }
      if (this.draft?.page === n || this.aim?.page === n) this.#drawMeasures(n);
    };
    snapReady
      .then(() => { if (!snap) throw new Error("snapping unavailable"); return doc.getPage(n); })
      .then((page) => page.getOperatorList({ annotationMode: pdfjs.AnnotationMode.DISABLE }))
      .then((ops) => snap.buildFromOperatorList(ops, pdfjs.OPS))
      .then((geometry) => done(geometry, null), (e) => done(null, e));
    return null;
  }

  /** Where a pointer event is on a page: its number, its view and the point
   *  on the page, in PDF units and on screen (relative to the page). While
   *  measuring, the point snaps to the drawing's lines (Alt: no snapping):
   *  ``choices`` says how many points there were to snap to there, and Tab
   *  picks the next one. With a point to go on from (``from``, else the last
   *  one placed), Shift keeps the line at a multiple of 45° and Straight
   *  only at a multiple of 90°. ``snapped`` says what it landed on, ``segs``
   *  the lines of the drawing it's on. */
  #pageAt(e, only = null, from = undefined) {
    const pageEl = e.target.closest?.(".page");
    const n = Number(pageEl?.dataset.pageNumber);
    if (!n || (only && n !== only)) return null;
    const view = this.pdfViewer.getPageView(n - 1);
    if (!view?.div || !view.viewport) return null;
    const box = view.div.getBoundingClientRect();
    const x = e.clientX - box.left - view.div.clientLeft, y = e.clientY - box.top - view.div.clientTop;
    const raw = view.viewport.convertToPdfPoint(x, y);
    if (!Number.isFinite(raw[0]) || !Number.isFinite(raw[1])) return null;
    let pdf = raw;
    if (from === undefined) {
      const pts = this.draft?.page === n ? this.draft.pts : null;
      from = pts?.length ? pts.at(-1) : null;
    }
    let ortho = null;
    const step = e.shiftKey ? Math.PI / 4 : this.straightOnly ? Math.PI / 2 : 0;
    if (step && from) {  // keep it straight
      ortho = Math.round(Math.atan2(pdf[1] - from[1], pdf[0] - from[0]) / step) * step;
      const len = Math.hypot(pdf[0] - from[0], pdf[1] - from[1]);
      pdf = [from[0] + len * Math.cos(ortho), from[1] + len * Math.sin(ortho)];
    }
    let snapped = null, segs = null, cands = [], index = 0;
    if (this.measureMode && !e.altKey) {
      try {
        const geometry = this.#geometry(n);
        cands = geometry?.candidates(pdf, SNAP_PX / view.viewport.scale, { from, ortho }) || [];
        if (cands.length) {
          const kept = this.pick && this.pick.page === n && Math.hypot(e.clientX - this.pick.x, e.clientY - this.pick.y) < 6;
          index = kept ? ((this.pick.index % cands.length) + cands.length) % cands.length : 0;
          const c = cands[index];
          if (Number.isFinite(c.x) && Number.isFinite(c.y)) { pdf = [c.x, c.y]; snapped = c.kind; segs = c.segs; }
        }
      } catch (err) {
        // Snapping failing must never stop a measurement: the point stays where it was clicked.
        this.activeTab.geometry?.set(n, null);
        cands = [];
      }
    }
    return { page: n, view, x, y, raw, pdf, snapped, segs, cands, index };
  }

  /** The measurement under the pointer: a point of it to move (``index``),
   *  or the whole of it (``index`` null: its line, edges or label). */
  #hitMeasure(e) {
    const pageEl = e.target.closest?.(".page");
    const n = Number(pageEl?.dataset.pageNumber);
    const items = (this.activeTab?.measures || []).filter((m) => m.page === n);
    if (!n || !items.length) return null;
    const view = this.pdfViewer.getPageView(n - 1);
    if (!view?.div || !view.viewport) return null;
    const box = view.div.getBoundingClientRect();
    const x = e.clientX - box.left - view.div.clientLeft, y = e.clientY - box.top - view.div.clientTop;
    const screen = (m) => m.pts.map((p) => view.viewport.convertToViewportPoint(...p));
    for (let k = items.length - 1; k >= 0; k--) {
      const pts = screen(items[k]);
      const i = pts.findIndex(([px, py]) => Math.hypot(px - x, py - y) <= 8);
      if (i >= 0) return { m: items[k], index: i, page: n };
    }
    const toSegment = ([ax, ay], [bx, by]) => {
      const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / len2)) : 0;
      return Math.hypot(ax + t * dx - x, ay + t * dy - y);
    };
    for (let k = items.length - 1; k >= 0; k--) {
      const m = items[k], pts = screen(m);
      const edges = m.kind === "area" ? pts.map((p, i) => [p, pts[(i + 1) % pts.length]])
        : m.kind === "count" ? [] : pts.slice(1).map((p, i) => [pts[i], p]);
      if (edges.some(([a, b]) => toSegment(a, b) <= 5)) return { m, index: null, page: n };
      const label = this.labelBoxes?.get(m.id);
      if (label && x >= label.x && x <= label.x + label.width && y >= label.y && y <= label.y + label.height) return { m, index: null, page: n };
    }
    return null;
  }

  #measureDown(e) {
    if (!this.measureMode || e.button !== 0) return;
    // On a measurement already made: drag it (or a point of it) to move it;
    // a click without dragging measures from there as usual.
    if (!this.draft && this.measureMode !== "calibrate") {
      const hit = this.#hitMeasure(e);
      if (hit) this.selected = { id: hit.m.id, index: hit.index };
      if (hit) {
        e.preventDefault();
        e.stopPropagation();
        const at = this.#pageAt(e, hit.page, null);
        this.pendingDrag = { hit, x: e.clientX, y: e.clientY, start: at?.raw,
          event: { target: e.target, clientX: e.clientX, clientY: e.clientY, shiftKey: e.shiftKey, altKey: e.altKey, button: 0,
            preventDefault() {}, stopPropagation() {} } };
        return;
      }
    }
    this.#measureClick(e);
  }

  #measureClick(e) {
    const at = this.#pageAt(e, this.draft?.page);
    if (!at) return;
    e.preventDefault();
    e.stopPropagation();
    if (this.measureMode === "count") { this.#countAt(at); return; }
    if (!this.draft) {
      this.draft = { page: at.page, kind: this.measureMode, pts: [at.pdf], hover: null };
      this.#drawMeasures(at.page);
      return;
    }
    const d = this.draft;
    if (d.kind === "angle" && d.pts.length < 2) { d.pts.push(at.pdf); this.#drawMeasures(at.page); return; }
    if (d.kind === "area" || d.kind === "path" || d.kind === "cutout") {
      const screen = (p) => at.view.viewport.convertToViewportPoint(...p);
      const [fx, fy] = screen(d.pts[0]), [lx, ly] = screen(d.pts.at(-1));
      const closed = d.kind !== "path";
      const nearFirst = closed && d.pts.length >= 3 && Math.hypot(at.x - fx, at.y - fy) < 9;
      const onLast = Math.hypot(at.x - lx, at.y - ly) < 4;  // the second click of a double-click
      if (nearFirst || (onLast && d.pts.length >= (closed ? 3 : 2))) { this.#finishMeasure(); return; }
      if (!onLast) d.pts.push(at.pdf);
      this.#drawMeasures(at.page);
      return;
    }
    d.pts.push(at.pdf);
    this.#finishMeasure();
  }

  #measureMove(e) {
    if (!this.measureMode) return;
    this.lastMove = e;
    if (this.pick && Math.hypot(e.clientX - this.pick.x, e.clientY - this.pick.y) >= 6) this.pick = null;
    if (this.pendingDrag && Math.hypot(e.clientX - this.pendingDrag.x, e.clientY - this.pendingDrag.y) > 3) {
      const { hit, start } = this.pendingDrag;
      this.pendingDrag = null;
      this.#rememberMeasures();
      this.dragging = { ...hit, start, orig: hit.m.pts.map((p) => [...p]), origHoles: hit.m.holes?.map((h) => h.map((p) => [...p])) };
      this.container.classList.add("rd-dragging");
    }
    if (this.pendingDrag) return;
    if (this.dragging) { this.#dragMeasure(e); return; }
    const at = this.#pageAt(e, this.draft?.page);
    const before = this.aim?.page;
    this.aim = at ? { page: at.page, x: at.x, y: at.y, raw: at.raw, pdf: at.pdf, snapped: at.snapped, segs: at.segs, cands: at.cands, index: at.index } : null;
    if (this.draft && at) this.draft.hover = at.pdf;
    if (!this.draft) {
      const hit = at && this.measureMode !== "calibrate" ? this.#hitMeasure(e) : null;
      this.container.style.cursor = hit ? (hit.index === null ? "grab" : "move") : "";
    }
    if (this.moveFrame) return;
    this.moveFrame = requestAnimationFrame(() => {
      this.moveFrame = null;
      const n = this.aim?.page || this.draft?.page;
      if (n) this.#drawMeasures(n);
      if (before && before !== n) this.#drawMeasures(before);
    });
  }

  /** Move a measurement, or one of its points, with the pointer. */
  #dragMeasure(e) {
    const g = this.dragging, m = g.m;
    if (g.index === null) {  // the whole of it: by how far the pointer went
      const at = this.#pageAt(e, g.page, null);
      if (!at || !g.start) return;
      const dx = at.raw[0] - g.start[0], dy = at.raw[1] - g.start[1];
      m.pts = g.orig.map(([x, y]) => [x + dx, y + dy]);
      if (g.origHoles) m.holes = g.origHoles.map((h) => h.map(([x, y]) => [x + dx, y + dy]));
      this.aim = null;
    } else {  // one point: it snaps, kept straight from its neighbour with Shift or Straight only
      const other = m.kind === "area" ? m.pts[(g.index + m.pts.length - 1) % m.pts.length]
        : m.kind === "path" ? m.pts[g.index ? g.index - 1 : 1]
        : m.kind === "angle" ? m.pts[g.index === 1 ? 0 : 1]
        : m.kind === "count" ? null : m.pts[1 - g.index];
      const at = this.#pageAt(e, g.page, other);
      if (!at) return;
      m.pts[g.index] = at.pdf;
      this.aim = { page: at.page, x: at.x, y: at.y, raw: at.raw, pdf: at.pdf, snapped: at.snapped, segs: at.segs, cands: at.cands, index: at.index };
    }
    if (this.moveFrame) return;
    this.moveFrame = requestAnimationFrame(() => { this.moveFrame = null; this.#drawMeasures(g.page); this.#renderMeasPanel(); });
  }

  #measureUp() {
    if (this.pendingDrag) {  // pressed on a measurement without moving: an ordinary click
      const { event } = this.pendingDrag;
      this.pendingDrag = null;
      this.#measureClick(event);
      return;
    }
    if (!this.dragging) return;
    const page = this.dragging.page;
    this.dragging = null;
    this.aim = null;
    this.container.classList.remove("rd-dragging");
    this.#drawMeasures(page);
    this.#renderMeasPanel();
  }

  async #finishMeasure() {
    const d = this.draft;
    this.draft = null;
    if (!d) return;
    if (d.kind === "calibrate") { this.#drawMeasures(d.page); await this.#scaleFromLine(d); return; }
    const least = { area: 3, cutout: 3, angle: 3 }[d.kind] || 2;
    if (d.pts.length < least) { this.#drawMeasures(d.page); return; }
    const tab = this.activeTab;
    tab.measures ||= [];
    if (d.kind === "cutout") { this.#addCutout(d); return; }
    const rect = d.kind === "rect";
    if (rect) {  // two corners on one line are no rectangle
      const view = this.pdfViewer.getPageView(d.page - 1)?.viewport;
      const [ax, ay] = view.convertToViewportPoint(...d.pts[0]), [bx, by] = view.convertToViewportPoint(...d.pts[1]);
      if (Math.abs(bx - ax) < 3 || Math.abs(by - ay) < 3) { this.#drawMeasures(d.page); this.toast("Click two opposite corners of the rectangle.", 3000); return; }
    }
    this.#rememberMeasures();
    const m = { id: ++measureSeq, page: d.page, kind: rect ? "area" : d.kind, pts: rect ? measure.rectangle(d.pts[0], d.pts[1]) : d.pts };
    if (tab.measureGroup) m.group = tab.measureGroup;
    tab.measures.push(m);
    this.selected = { id: m.id, index: m.pts.length - 1 };  // the arrow keys nudge the last point
    this.#drawMeasures(d.page);
    // The list opens with the first measurement, unless it was closed before.
    if (this.measPanel.hidden && !store.get("pdfm-measure-list-closed", false)) this.#toggleMeasPanel(true, false);
    else this.#renderMeasPanel();
  }

  /** Count mode: a click adds a numbered mark to the count being made on
   *  this page (a new count after Enter, or on another page). */
  #countAt(at) {
    const tab = this.activeTab;
    tab.measures ||= [];
    this.#rememberMeasures();
    let m = tab.measures.find((x) => x.id === this.countTarget && x.page === at.page);
    if (!m) {
      m = { id: ++measureSeq, page: at.page, kind: "count", pts: [] };
      if (tab.measureGroup) m.group = tab.measureGroup;
      tab.measures.push(m);
      this.countTarget = m.id;
    }
    m.pts.push(at.pdf);
    this.selected = { id: m.id, index: m.pts.length - 1 };
    this.#drawMeasures(at.page);
    if (this.measPanel.hidden && !store.get("pdfm-measure-list-closed", false)) this.#toggleMeasPanel(true, false);
    else this.#renderMeasPanel();
  }

  /** A cut-out (``d``, just drawn) comes off the area it's drawn in. */
  #addCutout(d) {
    const tab = this.activeTab, middle = measure.centre(d.pts);
    const area = [...tab.measures].reverse().find((m) => m.page === d.page && m.kind === "area" && measure.inside(middle, m.pts));
    if (!area) {
      this.#drawMeasures(d.page);
      this.toast("Draw the cut-out inside an area you've measured: it's taken off that area.", 5000);
      return;
    }
    this.#rememberMeasures();
    (area.holes ||= []).push(d.pts);
    this.selected = { id: area.id, index: null };
    this.#drawMeasures(d.page);
    this.#renderMeasPanel();
  }

  /** Set the scale from a line of known length (``d``, just drawn): ask how
   *  long it really is, show what scale that makes and the standard scale
   *  it matches, and set it for this sheet or every sheet. */
  async #scaleFromLine(d) {
    const length = measure.distance(d.pts[0], d.pts[1]) * (this.pdfViewer.getPageView(d.page - 1).pdfPage?.userUnit || 1);
    if (length < 1) return;
    const current = this.#pageScale(d.page);
    const units = current?.units || "ft";
    const name = (scale) => (scale.custom ? scale.label.replace(/^Set from a dimension \((.*)\)$/, "≈ $1") : scale.label);
    const now = el("p", { class: "rd-now" }, current
      ? `At the current scale (${name(current)}), that line measures ${measure.formatLength(length, current, this.units)}.`
      : `On the paper, that line is ${(length / 72).toFixed(3)} in long.`);
    const input = el("input", { type: "text", placeholder: units === "m" ? "e.g. 3.6 m or 3600 mm" : "e.g. 12'-6\" or 3.6 m", "aria-label": "Real length", autocomplete: "off" });
    const error = el("div", { class: "err", role: "alert", hidden: true }, "Type a length such as 12'-6\", 150\", 3.6 m or 3600 mm.");
    const result = el("div", { class: "rd-scaleresult", hidden: true });
    const radio = (group, value, label, checked) => el("label", { class: "rd-radio" },
      el("input", { type: "radio", name: group, value, checked: checked || null }), el("span", {}, label));
    const scope = el("fieldset", { class: "rd-scope" }, el("legend", {}, "Use it for"),
      radio("rd-scope", "page", `This sheet (page ${this.labels?.[d.page - 1] || d.page})`, true),
      radio("rd-scope", "all", `Every sheet in this document (${this.doc.numPages})`));
    let pick = null;  // what the typed length makes: { exact, near }
    const update = () => {
      error.hidden = true;
      const real = measure.parseLength(input.value, units);
      const ratio = real ? real.inches / (length / 72) : NaN;
      if (!real || !(ratio >= 0.05 && ratio <= 20000)) { pick = null; result.hidden = true; return; }
      const exact = measure.scaleFrom(length, real), near = measure.nearestScale(ratio, real.units);
      pick = { exact, near: near && near.off < 0.03 ? near : null };
      if (pick.near && pick.near.off < 0.001) {  // the same thing: no choice to make
        result.replaceChildren(el("p", {}, `That makes the scale ${pick.near.scale.label}, a standard scale.`));
        result.append(el("input", { type: "radio", name: "rd-which", value: "near", checked: true, hidden: true }));
      } else if (pick.near) {
        const pct = (pick.near.off * 100).toFixed(pick.near.off < 0.001 ? 2 : 1);
        result.replaceChildren(el("p", {}, "That makes the scale:"),
          radio("rd-which", "near", `${pick.near.scale.label}, the standard scale it matches (${pct}% off)`, pick.near.off < 0.01),
          radio("rd-which", "exact", `exactly as measured, ${name(exact)}`, pick.near.off >= 0.01));
      } else {
        result.replaceChildren(el("p", {}, `That makes the scale ${name(exact)} (no standard scale is close).`));
      }
      result.hidden = false;
    };
    input.addEventListener("input", update);
    for (;;) {
      const pending = this.#dialog("How long is that line?", [now, el("p", {}, "Type its real length, as the drawing gives it:"), input, error, result, scope],
        [["cancel", "Cancel"], ["ok", "Set the scale", true]]);
      input.focus();
      const answer = await pending;
      if (answer !== "ok") { this.measureTool(null); return; }
      update();
      if (pick) break;
      const real = measure.parseLength(input.value, units);
      const ratio = real ? real.inches / (length / 72) : NaN;
      error.textContent = real ? `That would make the scale 1:${ratio < 1 ? ratio.toFixed(3) : Math.round(ratio)}, which doesn't look like a drawing scale. Check the length and the points you clicked.`
        : "Type a length such as 12'-6\", 150\", 3.6 m or 3600 mm.";
      error.hidden = false;
    }
    const useNear = pick.near && result.querySelector("input[value=near]")?.checked;
    const scale = useNear ? pick.near.scale : pick.exact;
    const all = scope.querySelector("input[value=all]")?.checked;
    if (all) {
      const tab = this.activeTab;
      tab.scales ||= {};
      for (let p = 1; p <= this.doc.numPages; p++) tab.scales[p] = scale;
      tab.scales["*"] = scale;
      store.set(this.#scaleKey(), tab.scales);
      this.#showScale();
    } else {
      this.#setPageScale(d.page, scale);
    }
    this.measureTool("distance");
    this.toast(`Scale set to ${name(scale)}${all ? " for every sheet" : ""}. Click two points to measure.`, 5000);
  }

  /** Esc, Enter, Backspace and Tab while measuring. True if the key was used. */
  #measureKey(e) {
    if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.key === "Tab" && this.aim?.cands?.length > 1 && this.lastMove) {
      // Lines close together: the next point there is to snap to.
      const step = e.shiftKey ? -1 : 1;
      this.pick = { page: this.aim.page, x: this.lastMove.clientX, y: this.lastMove.clientY, index: (this.aim.index || 0) + step };
      this.#measureMove(this.lastMove);
      return true;
    }
    if (e.key === "Escape") {
      if (this.draft) { const n = this.draft.page; this.draft = null; this.#drawMeasures(n); }
      else if (this.selected) { const m = this.#selectedMeasure(); this.selected = null; if (m) this.#drawMeasures(m.page); }
      else this.measureTool(null);
      return true;
    }
    if (e.key === "Enter" && ["area", "path", "cutout"].includes(this.draft?.kind)) { this.#finishMeasure(); return true; }
    if (e.key === "Enter" && this.measureMode === "count") {
      if (this.countTarget) this.toast("Next clicks start a new count", 1500);
      this.countTarget = null;
      return true;
    }
    if (e.key.startsWith("Arrow") && !this.draft && this.#selectedMeasure()) {
      const step = e.shiftKey ? 10 : 1;
      const [dx, dy] = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      this.#nudge(dx, dy);
      return true;
    }
    if (e.key === "Backspace" || e.key === "Delete") {
      if (this.draft && this.draft.pts.length > 1) { this.draft.pts.pop(); this.#drawMeasures(this.draft.page); }
      else if (this.draft) { const n = this.draft.page; this.draft = null; this.#drawMeasures(n); }
      else if (this.#selectedMeasure()?.kind === "count" && this.#selectedMeasure().pts.length > 1) {  // one mark of a count
        const m = this.#selectedMeasure();
        this.#rememberMeasures();
        m.pts.splice(Number.isInteger(this.selected.index) ? this.selected.index : m.pts.length - 1, 1);
        this.selected = { id: m.id, index: m.pts.length - 1 };
        this.#drawMeasures(m.page);
        this.#renderMeasPanel();
      } else if (this.#selectedMeasure()) this.#deleteMeasure(this.selected.id);
      else if (this.activeTab?.measures?.length) this.#deleteMeasure(this.activeTab.measures.at(-1).id);
      return true;
    }
    return false;
  }

  #selectedMeasure() {
    return this.selected ? (this.activeTab?.measures || []).find((m) => m.id === this.selected.id) || null : null;
  }

  /** Move the selected point (or measurement) by (dx, dy) pixels on screen. */
  #nudge(dx, dy) {
    const m = this.#selectedMeasure();
    const view = m && this.pdfViewer.getPageView(m.page - 1);
    if (!view?.viewport) return;
    const vp = view.viewport;
    const [x0, y0] = vp.convertToPdfPoint(0, 0), [x1, y1] = vp.convertToPdfPoint(dx, dy);
    const ddx = x1 - x0, ddy = y1 - y0;
    // Arrow presses close together are one step to undo.
    const now = performance.now();
    if (!this.nudging || this.nudging.id !== m.id || this.nudging.index !== this.selected.index || now - this.nudging.at > 1200) this.#rememberMeasures();
    this.nudging = { id: m.id, index: this.selected.index, at: now };
    const move = (p) => [p[0] + ddx, p[1] + ddy];
    if (this.selected.index === null || this.selected.index === undefined) {
      m.pts = m.pts.map(move);
      if (m.holes) m.holes = m.holes.map((h) => h.map(move));
    }
    else m.pts[this.selected.index] = move(m.pts[this.selected.index]);
    this.#drawMeasures(m.page);
    this.#renderMeasPanel();
  }

  // ---- undo and redo for measuring ----------------------------------------------------
  /** Keep how the measurements are now, before changing them. */
  #rememberMeasures() {
    const tab = this.activeTab;
    if (!tab) return;
    tab.measureHistory ||= { undo: [], redo: [] };
    tab.measureHistory.undo.push(JSON.stringify(tab.measures || []));
    if (tab.measureHistory.undo.length > 200) tab.measureHistory.undo.shift();
    tab.measureHistory.redo = [];
    this.nudging = null;
  }

  /** Ctrl+Z (or Ctrl+Y / Ctrl+Shift+Z with ``redo``) for measurements.
   *  True if there was something to take back (or bring back). */
  #measureUndo(redo) {
    const tab = this.activeTab, h = tab?.measureHistory;
    const from = redo ? h?.redo : h?.undo, to = redo ? h?.undo : h?.redo;
    if (!from?.length) return false;
    to.push(JSON.stringify(tab.measures || []));
    const pages = new Set((tab.measures || []).map((m) => m.page));
    tab.measures = JSON.parse(from.pop());
    for (const m of tab.measures) { pages.add(m.page); measureSeq = Math.max(measureSeq, m.id || 0); }
    this.selected = null;
    this.nudging = null;
    this.draft = null;
    for (const n of pages) this.#drawMeasures(n);
    this.#renderMeasPanel();
    this.toast(redo ? "Measurement change redone" : "Measurement change undone", 1500);
    return true;
  }

  #drawAllMeasures() {
    if (!this.pdfViewer || !this.doc) return;
    const pages = new Set([...(this.activeTab?.measures || []).map((m) => m.page), ...(this.draft ? [this.draft.page] : [])]);
    for (const layer of this.viewerEl.querySelectorAll("svg.rd-measures")) pages.add(Number(layer.closest(".page")?.dataset.pageNumber));
    for (const n of pages) if (n) this.#drawMeasures(n);
    this.#renderMeasPanel();
  }

  /** Draw page ``n``'s measurements (and the one being made) over it. */
  #drawMeasures(n) {
    try { this.#drawMeasuresOn(n); } catch (e) { /* a bad point or scale: the page itself is unaffected */ }
  }

  /** A measurement's value: { text, sub, real (inches or square inches,
   *  when the sheet has a scale), units (the sheet's) }. */
  #measureValue(m) {
    const scale = this.#pageScale(m.page);
    const uu = this.pdfViewer.getPageView(m.page - 1)?.pdfPage?.userUnit || 1;
    if (m.kind === "count") {
      const n = m.pts.length;
      return { text: `${n} ${n === 1 ? "item" : "items"}`, sub: "", real: null, count: n, units: scale?.units };
    }
    if (m.kind === "angle") {
      const deg = measure.angle(m.pts[0], m.pts[1], m.pts[2]);
      return { text: `${deg.toFixed(1)}°`, sub: "", real: null, angle: deg, units: scale?.units };
    }
    if (m.kind === "area") {
      const a = measure.netArea(m.pts, m.holes) * uu * uu, holes = m.holes?.length || 0;
      return { text: measure.formatArea(a, scale, this.units),
        sub: "perimeter " + measure.formatLength(measure.perimeter(m.pts) * uu, scale, this.units)
          + (holes ? ` · less ${holes} cut-out${holes === 1 ? "" : "s"}` : ""),
        real: scale ? a / 5184 * scale.ratio * scale.ratio : null, units: scale?.units };
    }
    const len = measure.pathLength(m.pts) * uu;
    return { text: measure.formatLength(len, scale, this.units), sub: m.kind === "path" ? `${m.pts.length - 1} segments` : "",
      real: scale ? measure.realInches(len, scale) : null, units: scale?.units };
  }

  /** The colour of measurement group ``name`` (by the order groups were
   *  made); null for no group. */
  #groupColor(name) {
    if (!name) return null;
    const groups = this.activeTab?.measureGroups || [];
    const i = groups.indexOf(name);
    return GROUP_COLORS[(i < 0 ? groups.length : i) % GROUP_COLORS.length];
  }

  #drawMeasuresOn(n) {
    if (!this.pdfViewer || !this.doc) return;
    const view = this.pdfViewer.getPageView(n - 1);
    if (!view?.div) return;
    const items = (this.activeTab?.measures || []).filter((m) => m.page === n);
    const d = this.draft?.page === n ? this.draft : null;
    const aim = this.measureMode && this.aim?.page === n ? this.aim : null;
    let layer = view.div.querySelector(":scope > svg.rd-measures");
    if (!items.length && !d && !aim) { layer?.remove(); return; }
    const NS = "http://www.w3.org/2000/svg";
    const make = (tag, attrs = {}) => { const node = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); return node; };
    if (!layer) { layer = make("svg", { class: "rd-measures", "aria-hidden": "true" }); view.div.append(layer); }
    layer.replaceChildren();
    const vp = view.viewport, scale = this.#pageScale(n);
    const uu = view.pdfPage?.userUnit || 1;  // a page can declare its unit bigger than a point
    const length = (pts) => measure.formatLength(pts * uu, scale, this.units);
    const area = (pts2) => measure.formatArea(pts2 * uu * uu, scale, this.units);
    const xy = (p) => vp.convertToViewportPoint(...p);
    let into = layer;  // where the measurement being drawn goes (its own group, in its colour)
    this.labelBoxes ||= new Map();
    // A label on a white backing, so lines of the drawing don't run through it.
    let named = null;  // the name of the measurement being drawn, on its main label
    const label = (x, y, lines, cls = "", id = null) => {
      if (id !== null && named) lines = [named, ...lines];
      const g = make("g");
      const text = make("text", { x, y, class: "rd-measure-label" + cls, "text-anchor": "middle" });
      lines.forEach((line, i) => {
        const span = make("tspan", { x, dy: i ? "1.25em" : `${0.35 - (lines.length - 1) * 0.62}em` });
        span.textContent = line;
        text.append(span);
      });
      g.append(text);
      into.append(g);
      try {
        const b = text.getBBox();
        const pill = { x: b.x - 6, y: b.y - 3, width: b.width + 12, height: b.height + 6 };
        g.insertBefore(make("rect", { ...pill, rx: 6, class: "rd-measure-pill" + cls }), text);
        if (id !== null) this.labelBoxes.set(id, pill);
      } catch (err) { /* not laid out: the text alone */ }
    };
    const dot = (x, y, picked = false) => into.append(make("circle", { cx: x, cy: y, r: picked ? 5.5 : 3.5, class: "rd-measure-dot" + (picked ? " picked" : "") }));
    const sel = this.measureMode ? this.selected : null;
    const isPicked = (id, i) => sel && sel.id === id && (sel.index === null || sel.index === undefined || sel.index === i);
    const drawLine = (a, b, { draft = false, calibrate = false, hot = "", id = null } = {}) => {
      const [ax, ay] = xy(a), [bx, by] = xy(b);
      into.append(make("line", { x1: ax, y1: ay, x2: bx, y2: by, class: "rd-measure-line" + (draft ? " draft" : "") + (calibrate ? " calibrate" : "") + hot }));
      dot(ax, ay, isPicked(id, 0)); dot(bx, by, isPicked(id, 1));
      if (calibrate || Math.hypot(bx - ax, by - ay) < 2) return;
      // the label beside the line's middle, on its upper side
      let nx = -(by - ay), ny = bx - ax;
      const len = Math.hypot(nx, ny);
      nx /= len; ny /= len;
      if (ny > 0) { nx = -nx; ny = -ny; }
      label((ax + bx) / 2 + nx * 15, (ay + by) / 2 + ny * 15, [length(measure.distance(a, b))], hot, id);
    };
    const drawArea = (pts, { draft = false, hot = "", id = null, holes = [], cutout = false } = {}) => {
      const screen = pts.map(xy);
      if (holes.length) {  // the area with its openings left out
        const ring = (ps) => "M" + ps.map((p) => xy(p).join(" ")).join("L") + "Z";
        into.append(make("path", { d: ring(pts) + holes.map(ring).join(""), "fill-rule": "evenodd", class: "rd-measure-area" + hot }));
        for (const h of holes) into.append(make("polygon", { points: h.map((p) => xy(p).join(",")).join(" "), class: "rd-measure-hole" }));
      } else {
        into.append(make(draft ? "polyline" : "polygon", { points: screen.map((p) => p.join(",")).join(" "),
          class: "rd-measure-area" + (draft ? " draft" : "") + (cutout ? " cutout" : "") + hot }));
      }
      screen.forEach(([x, y], i) => dot(x, y, isPicked(id, i)));
      if (pts.length < 3) return;
      const [cx, cy] = measure.centre(screen);
      if (cutout) { label(cx, cy, ["cut-out " + area(measure.polygonArea(pts))], " area"); return; }
      label(cx, cy, [area(measure.netArea(pts, holes)), "perimeter " + length(measure.perimeter(pts))], " area" + hot, id);
    };
    // Counted things: a numbered mark on each, the count by the first.
    const drawCount = (pts, { hot = "", id = null } = {}) => {
      pts.map(xy).forEach(([x, y], i) => {
        into.append(make("circle", { cx: x, cy: y, r: isPicked(id, i) ? 11 : 9, class: "rd-measure-count" + hot }));
        const t = make("text", { x, y, class: "rd-measure-countnum", "text-anchor": "middle", dy: "0.35em" });
        t.textContent = String(i + 1);
        into.append(t);
      });
      if (!pts.length) return;
      const [x, y] = xy(pts[0]);
      label(x, y - 24, [`${pts.length} ${pts.length === 1 ? "item" : "items"}`], hot, id);
    };
    // An angle: its two arms from the corner, an arc between them, the degrees.
    const drawAngle = (pts, { draft = false, hot = "", id = null } = {}) => {
      const screen = pts.map(xy);
      if (screen.length > 1) {
        for (const i of [0, 2]) {
          if (!screen[i]) continue;
          const [ax, ay] = screen[1], [bx, by] = screen[i];
          into.append(make("line", { x1: ax, y1: ay, x2: bx, y2: by, class: "rd-measure-line" + (draft ? " draft" : "") + hot }));
        }
      }
      screen.forEach(([x, y], i) => dot(x, y, isPicked(id, i)));
      if (screen.length < 3) return;
      const [vx, vy] = screen[1];
      const a1 = Math.atan2(screen[0][1] - vy, screen[0][0] - vx), a2 = Math.atan2(screen[2][1] - vy, screen[2][0] - vx);
      let span = a2 - a1;
      while (span > Math.PI) span -= 2 * Math.PI;
      while (span < -Math.PI) span += 2 * Math.PI;
      const r = 26, mid = a1 + span / 2;
      into.append(make("path", { d: `M${vx + r * Math.cos(a1)} ${vy + r * Math.sin(a1)}A${r} ${r} 0 0 ${span > 0 ? 1 : 0} ${vx + r * Math.cos(a2)} ${vy + r * Math.sin(a2)}`,
        class: "rd-measure-arc" + hot }));
      label(vx + 50 * Math.cos(mid), vy + 50 * Math.sin(mid), [`${measure.angle(pts[0], pts[1], pts[2]).toFixed(1)}°`], hot, id);
    };
    // A run of lines: each segment's length beside it, the total at its end.
    const drawPath = (pts, { draft = false, hot = "", id = null } = {}) => {
      const screen = pts.map(xy);
      into.append(make("polyline", { points: screen.map((p) => p.join(",")).join(" "), class: "rd-measure-line rd-measure-path" + (draft ? " draft" : "") + hot }));
      screen.forEach(([x, y], i) => dot(x, y, isPicked(id, i)));
      if (pts.length < 2) return;
      if (pts.length > 2) {
        for (let i = 1; i < pts.length; i++) {
          const [ax, ay] = screen[i - 1], [bx, by] = screen[i];
          if (Math.hypot(bx - ax, by - ay) < 60) continue;  // too short for its own label
          let nx = -(by - ay), ny = bx - ax;
          const len = Math.hypot(nx, ny);
          nx /= len; ny /= len;
          if (ny > 0) { nx = -nx; ny = -ny; }
          label((ax + bx) / 2 + nx * 13, (ay + by) / 2 + ny * 13, [length(measure.distance(pts[i - 1], pts[i]))], " seg" + hot);
        }
      }
      const [ex, ey] = screen.at(-1), [px, py] = screen.at(-2);
      const ux = ex - px, uy = ey - py, ul = Math.hypot(ux, uy) || 1;
      label(ex + (ux / ul) * 34, ey + (uy / ul) * 18 - 4, [(pts.length > 2 ? "total " : "") + length(measure.pathLength(pts))], " total" + hot, id);
    };
    for (const m of items) {
      const opts = { hot: m.id === this.hot || m === this.dragging?.m || (sel && sel.id === m.id) ? " hot" : "", id: m.id };
      const color = this.#groupColor(m.group);
      into = make("g", color ? { style: `--mc:${color}` } : {});
      layer.append(into);
      named = m.label || null;
      if (m.kind === "area") drawArea(m.pts, { ...opts, holes: m.holes || [] });
      else if (m.kind === "path") drawPath(m.pts, opts);
      else if (m.kind === "count") drawCount(m.pts, opts);
      else if (m.kind === "angle") drawAngle(m.pts, opts);
      else drawLine(m.pts[0], m.pts[1], opts);
    }
    into = layer;
    named = null;
    if (d) {
      const pts = d.hover ? [...d.pts, d.hover] : d.pts;
      const color = this.#groupColor(this.activeTab?.measureGroup);
      if (color) { into = make("g", { style: `--mc:${color}` }); layer.append(into); }
      if (d.kind === "area") drawArea(pts, { draft: true });
      else if (d.kind === "cutout") drawArea(pts, { draft: true, cutout: true });
      else if (d.kind === "rect") { if (pts.length > 1) drawArea(measure.rectangle(pts[0], pts[1]), { draft: true }); else { const [x, y] = xy(pts[0]); dot(x, y); } }
      else if (d.kind === "path") drawPath(pts, { draft: true });
      else if (d.kind === "angle") drawAngle(pts, { draft: true });
      else if (pts.length > 1) drawLine(pts[0], pts[1], { draft: true, calibrate: d.kind === "calibrate" });
      else { const [x, y] = xy(pts[0]); dot(x, y); }
      into = layer;
    }
    if (!aim || !aim.snapped) return;
    // What the next click would land on: the line(s) it's on, a mark, and
    // when there's more than one thing close by, a magnified view and Tab.
    const geometry = this.activeTab?.geometry?.get(n) || null;
    if (geometry && aim.segs) {
      for (const i of aim.segs) {
        const [x1, y1, x2, y2] = geometry.segment(i);
        const [sx, sy] = xy([x1, y1]), [ex, ey] = xy([x2, y2]);
        layer.append(make("line", { x1: sx, y1: sy, x2: ex, y2: ey, class: "rd-snap-seg" }));
      }
    }
    const [x, y] = xy(aim.pdf), r = 7;
    const shapes = {
      end: () => make("rect", { x: x - r, y: y - r, width: 2 * r, height: 2 * r }),
      cross: () => make("path", { d: `M${x - r} ${y - r}L${x + r} ${y + r}M${x + r} ${y - r}L${x - r} ${y + r}` }),
      mid: () => make("path", { d: `M${x} ${y - r}L${x + r} ${y + r}L${x - r} ${y + r}Z` }),
      perp: () => make("path", { d: `M${x - r} ${y - r}V${y + r}H${x + r}M${x - r} ${y}H${x}V${y + r}` }),
      on: () => make("circle", { cx: x, cy: y, r }),
    };
    const mark = (shapes[aim.snapped] || shapes.on)();
    mark.setAttribute("class", "rd-snap");
    layer.append(mark);
    const words = { end: "end of line", cross: "crossing", mid: "middle", perp: "perpendicular", on: "on line" };
    const many = aim.cands?.length > 1;
    const hint = make("text", { x: x + 12, y: y - 10, class: "rd-measure-label rd-snap-hint" });
    hint.textContent = words[aim.snapped] + (many ? `  ·  Tab: next (${aim.index + 1} of ${aim.cands.length})` : "");
    layer.append(hint);
    if (many && geometry) this.#drawLoupe(layer, make, geometry, vp, aim);
  }

  /** A magnified view of the drawing's lines around the pointer, with the
   *  points there are to snap to and the one chosen, for lines close together. */
  #drawLoupe(layer, make, geometry, vp, aim) {
    const R = 58, Z = 5;  // radius on screen, magnification
    const [cx, cy] = [aim.x, aim.y];
    const ox = cx + R + 28, oy = cy - R - 28;  // up and to the right of the pointer
    const toLoupe = (p) => { const [sx, sy] = vp.convertToViewportPoint(...p); return [ox + (sx - cx) * Z, oy + (sy - cy) * Z]; };
    const id = `rd-loupe-${aim.page}`;
    const g = make("g", { class: "rd-loupe" });
    const clip = make("clipPath", { id });
    clip.append(make("circle", { cx: ox, cy: oy, r: R }));
    g.append(clip, make("circle", { cx: ox, cy: oy, r: R, class: "rd-loupe-bg" }));
    const inner = make("g", { "clip-path": `url(#${id})` });
    const reach = (R / Z + 2) / vp.scale;
    const chosen = new Set(aim.segs || []);
    let drawn = 0;
    for (const i of geometry.near(aim.raw[0], aim.raw[1], reach)) {
      if (drawn++ > 400) break;
      const [x1, y1, x2, y2] = geometry.segment(i);
      const [ax, ay] = toLoupe([x1, y1]), [bx, by] = toLoupe([x2, y2]);
      inner.append(make("line", { x1: ax, y1: ay, x2: bx, y2: by, class: chosen.has(i) ? "rd-loupe-target" : "rd-loupe-line" }));
    }
    for (const [k, c] of (aim.cands || []).slice(0, 12).entries()) {
      const [px, py] = toLoupe([c.x, c.y]);
      inner.append(make("circle", { cx: px, cy: py, r: k === aim.index ? 5 : 3.5, class: k === aim.index ? "rd-loupe-pick" : "rd-loupe-cand" }));
    }
    const [rx, ry] = toLoupe(aim.raw);  // where the pointer itself is
    inner.append(make("path", { d: `M${rx - 7} ${ry}H${rx + 7}M${rx} ${ry - 7}V${ry + 7}`, class: "rd-loupe-cross" }));
    g.append(inner, make("circle", { cx: ox, cy: oy, r: R, class: "rd-loupe-ring" }));
    layer.append(g);
  }

  // ---- the list of measurements ---------------------------------------------------
  #toggleMeasPanel(open = this.measPanel.hidden, byHand = true) {
    this.measPanel.hidden = !open;
    this.listBtn.setAttribute("aria-pressed", String(open));
    if (byHand) store.set("pdfm-measure-list-closed", !open);
    this.#renderMeasPanel();
  }

  /** Lengths, areas and counts of ``list`` added up (in real units). */
  #totals(list) {
    const t = { lengths: 0, nLengths: 0, areas: 0, nAreas: 0, items: 0, nCounts: 0, unscaled: 0, sheet: null };
    for (const m of list) {
      const v = this.#measureValue(m);
      if (m.kind === "count") { t.items += v.count; t.nCounts++; continue; }
      if (m.kind === "angle") continue;
      if (v.real === null) { t.unscaled++; continue; }
      t.sheet ||= v.units;
      if (m.kind === "area") { t.areas += v.real; t.nAreas++; } else { t.lengths += v.real; t.nLengths++; }
    }
    return t;
  }

  #totalsText(t) {
    return [t.nLengths ? measure.formatInches(t.lengths, this.units, t.sheet) : "", t.nAreas ? measure.formatSquareInches(t.areas, this.units, t.sheet) : "",
      t.nCounts ? `${t.items} ${t.items === 1 ? "item" : "items"}` : ""].filter(Boolean).join(" · ");
  }

  #renderMeasPanel() {
    if (!this.measPanel) return;
    const tab = this.activeTab && this.doc ? this.activeTab : null;
    const items = tab?.measures || [];
    this.#persistMeasures();
    this.listCount.hidden = !items.length;
    this.listCount.textContent = String(items.length);
    if (this.measPanel.hidden) return;
    const units = el("select", { class: "rd-zoom", "aria-label": "Units" }, ...measure.UNITS.map(([v, l]) => el("option", { value: v }, l)));
    units.value = this.units;
    units.addEventListener("change", () => { this.units = units.value; store.set("pdfm-measure-units", this.units); this.#drawAllMeasures(); });
    const close = el("button", { class: "rd-btn", type: "button", title: "Close the list", "aria-label": "Close the list", html: svg("x") });
    close.addEventListener("click", () => this.#toggleMeasPanel(false));
    const head = el("div", { class: "rd-mhead" }, el("strong", {}, "Measurements"), close);
    const unitRow = el("label", { class: "rd-munits" }, "Show in", units);
    // The group new measurements go in (each group has its colour and subtotal).
    const groups = tab?.measureGroups || [];
    const groupSel = el("select", { class: "rd-zoom", "aria-label": "Group for new measurements" },
      el("option", { value: "" }, "No group"), ...groups.map((g) => el("option", { value: g }, g)), el("option", { value: "\u0000new" }, "New group…"));
    groupSel.value = tab?.measureGroup || "";
    groupSel.addEventListener("change", async () => {
      if (!tab) return;
      if (groupSel.value === "\u0000new") {
        const name = await this.#askGroupName();
        if (name) { if (!groups.includes(name)) (tab.measureGroups ||= []).push(name); tab.measureGroup = name; }
      } else tab.measureGroup = groupSel.value || null;
      this.#renderMeasPanel();
    });
    const groupRow = el("label", { class: "rd-munits" }, "New ones go in", groupSel);
    if (!items.length) {
      this.measPanel.replaceChildren(head, unitRow, groupRow, el("p", { class: "rd-mempty" },
        "Click two points with Distance, along a run of walls with Path, the corners of a room with Area (or two corners with Rectangle), "
        + "openings inside an area with Cut-out, each fixture with Count, or three points with Angle. Drag a measurement to move it, or a point of it "
        + "to change it; arrow keys nudge the selected point. Ctrl+Z undoes. Measurements are kept with the file on this computer."));
      return;
    }
    const kindIcon = { area: "area", path: "path", count: "count", angle: "angle" };
    const row = (m, i) => {
      const v = this.#measureValue(m);
      const del = el("button", { class: "rd-mdel", type: "button", title: "Delete this measurement", "aria-label": `Delete measurement ${i + 1}`, html: svg("x") });
      del.addEventListener("click", (e) => { e.stopPropagation(); this.#deleteMeasure(m.id); });
      const edit = el("button", { class: "rd-mdel", type: "button", title: "Name it, or put it in a group", "aria-label": `Name measurement ${i + 1}`, html: svg("pen") });
      edit.addEventListener("click", (e) => { e.stopPropagation(); this.#editMeasure(m); });
      const color = this.#groupColor(m.group);
      const li = el("li", { class: "rd-mrow" + (this.hot === m.id ? " hot" : ""), tabindex: "0", title: "Show it on the page", style: color ? `--mc:${color}` : null },
        el("span", { class: "rd-mkind", html: svg(kindIcon[m.kind] || "ruler") }),
        el("span", { class: "rd-mval" }, m.label ? el("em", {}, m.label) : null, v.text, v.sub ? el("small", {}, v.sub) : null),
        el("span", { class: "rd-mpage" }, `p. ${this.labels?.[m.page - 1] || m.page}`), edit, del);
      li.addEventListener("mouseenter", () => this.#hotMeasure(m.id));
      li.addEventListener("mouseleave", () => this.#hotMeasure(null));
      li.addEventListener("click", () => { this.selected = { id: m.id, index: null }; this.#showMeasure(m); });
      li.addEventListener("dblclick", () => this.#editMeasure(m));
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") this.#showMeasure(m); if (e.key === "Delete") this.#deleteMeasure(m.id); if (e.key === "F2") this.#editMeasure(m); });
      return li;
    };
    // By group (in the order the groups were made), the ungrouped first.
    const sections = [null, ...groups].map((g) => [g, items.filter((m) => (m.group || null) === g)]).filter(([, list]) => list.length);
    const grouped = sections.some(([g]) => g);
    let k = 0;
    const list = el("ol", { class: "rd-mlist" });
    for (const [g, members] of sections) {
      if (grouped) {
        const color = this.#groupColor(g);
        list.append(el("li", { class: "rd-mgroup", style: color ? `--mc:${color}` : null },
          el("i", { class: "rd-mgroupdot" }), el("b", {}, g || "No group"), el("span", {}, this.#totalsText(this.#totals(members)))));
      }
      for (const m of members) list.append(row(m, k++));
    }
    const t = this.#totals(items), totals = [];
    if (t.nLengths > 1) totals.push(el("div", {}, el("span", {}, `Total length (${t.nLengths})`), el("b", {}, measure.formatInches(t.lengths, this.units, t.sheet))));
    if (t.nAreas > 1) totals.push(el("div", {}, el("span", {}, `Total area (${t.nAreas})`), el("b", {}, measure.formatSquareInches(t.areas, this.units, t.sheet))));
    if (t.nCounts > 1) totals.push(el("div", {}, el("span", {}, `Total count (${t.nCounts})`), el("b", {}, `${t.items} items`)));
    if (t.unscaled && totals.length) totals.push(el("small", {}, `${t.unscaled} on a sheet without a scale ${t.unscaled === 1 ? "is" : "are"} left out.`));
    const button = (icon, label, title, fn) => { const b = el("button", { class: "rd-btn", type: "button", title, html: svg(icon) + `<span>${label}</span>` }); b.addEventListener("click", fn); return b; };
    const foot = el("div", { class: "rd-mfoot" },
      button("copy", "Copy", "Copy the measurements, to paste in a spreadsheet or an email", () => this.#copyMeasures()),
      button("csv", "CSV", "Save the measurements as a CSV file, for Excel or an estimate", () => this.#exportMeasures()),
      button("pdfin", "Into PDF", "Write the measurements into the PDF as measurement annotations, so they show in any viewer and print", () => this.#measuresIntoPdf()),
      el("span", { style: "flex:1" }),
      button("trash", "", "Remove every measurement", () => this.clearMeasures()));
    this.measPanel.replaceChildren(...[head, unitRow, groupRow, list, totals.length ? el("div", { class: "rd-mtotals" }, ...totals) : null, foot].filter(Boolean));
  }

  /** Ask for a new group's name. */
  async #askGroupName(value = "") {
    const input = el("input", { type: "text", value, placeholder: "e.g. Bedroom floors, Outlets", "aria-label": "Group name" });
    setTimeout(() => input.focus(), 0);
    const answer = await this.#dialog("New group", [el("p", {}, "Measurements in a group share a colour and are added up together."), input],
      [["cancel", "Cancel"], ["ok", "Make the group", true]]);
    return answer === "ok" ? input.value.trim().slice(0, 60) : "";
  }

  /** Name a measurement and pick its group. */
  async #editMeasure(m) {
    const tab = this.activeTab;
    if (!tab) return;
    const name = el("input", { type: "text", value: m.label || "", placeholder: "e.g. Living room, North wall", "aria-label": "Name" });
    const groups = tab.measureGroups || [];
    const group = el("select", { "aria-label": "Group" }, el("option", { value: "" }, "No group"), ...groups.map((g) => el("option", { value: g }, g)),
      el("option", { value: "\u0000new" }, "New group…"));
    group.value = m.group || "";
    setTimeout(() => name.select(), 0);
    const answer = await this.#dialog("Measurement", [el("label", { class: "rd-field" }, "Name", name), el("label", { class: "rd-field" }, "Group", group)],
      [["cancel", "Cancel"], ["ok", "Save", true]]);
    if (answer !== "ok") return;
    let g = group.value;
    if (g === "\u0000new") {
      g = await this.#askGroupName();
      if (g && !groups.includes(g)) (tab.measureGroups ||= []).push(g);
    }
    this.#rememberMeasures();
    m.label = name.value.trim().slice(0, 80) || undefined;
    m.group = g || undefined;
    this.#drawMeasures(m.page);
    this.#renderMeasPanel();
  }

  // ---- measurements kept with the file, as a spreadsheet, in the PDF ------------------
  /** Where a document's measurements are kept on this computer: by the
   *  file's place (a saved file keeps them), else by the document itself. */
  #measuresStore(tab = this.activeTab) {
    if (!tab) return null;
    if (tab.path) return "pdfm-measures:path:" + tab.path.toLowerCase();
    return tab === this.activeTab && this.doc ? "pdfm-measures:doc:" + this.doc.fingerprints[0] : null;
  }

  #persistMeasures() {
    const tab = this.activeTab, key = tab?.measuresLoaded && this.#measuresStore(tab);
    if (!key) return;
    const data = { measures: tab.measures || [], groups: tab.measureGroups || [], group: tab.measureGroup || null };
    const text = JSON.stringify(data);
    if (text === tab.measuresSaved) return;
    tab.measuresSaved = text;
    if (!data.measures.length && !data.groups.length) { try { localStorage.removeItem(key); } catch (e) { /* no storage */ } return; }
    store.set(key, data);
  }

  /** Bring back the measurements made on this document before. */
  #loadMeasures() {
    const tab = this.activeTab, key = this.#measuresStore(tab);
    if (!tab || tab.measuresLoaded || !key) return;
    tab.measuresLoaded = true;
    const data = store.get(key, null);
    const ok = (m) => m && Number.isInteger(m.page) && m.page >= 1 && m.page <= this.doc.numPages && Array.isArray(m.pts)
      && m.pts.every((p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite)) && MEASURE_KINDS[m.kind];
    const kept = Array.isArray(data?.measures) ? data.measures.filter(ok) : [];
    if (tab.measures?.length || (!kept.length && !data?.groups?.length)) return;
    for (const m of kept) measureSeq = Math.max(measureSeq, m.id || 0);
    tab.measures = kept.map((m) => ({ ...m, id: m.id || ++measureSeq }));
    tab.measureGroups = Array.isArray(data.groups) ? data.groups.filter((g) => typeof g === "string") : [];
    tab.measureGroup = tab.measureGroups.includes(data.group) ? data.group : null;
    tab.measuresSaved = JSON.stringify({ measures: tab.measures, groups: tab.measureGroups, group: tab.measureGroup });
    if (kept.length) {
      this.listCount.hidden = false;
      this.listCount.textContent = String(kept.length);
      this.toast(`${kept.length} measurement${kept.length === 1 ? "" : "s"} from last time are back.`, 5000,
        ["Show", () => { this.toggleScaleBar(true); this.#toggleMeasPanel(true); }]);
    }
  }

  /** The measurements as rows of a table (for Copy and CSV). */
  #measureRows() {
    const rows = [["Group", "Name", "Page", "Kind", "Measured", "Quantity", "Unit", "Perimeter"]];
    for (const m of this.activeTab?.measures || []) {
      const v = this.#measureValue(m);
      let quantity = "", unit = "";
      if (m.kind === "count") [quantity, unit] = [v.count, "items"];
      else if (m.kind === "angle") [quantity, unit] = [+v.angle.toFixed(2), "°"];
      else if (v.real !== null) [quantity, unit] = measure.plainNumber(v.real, m.kind === "area", v.units);
      rows.push([m.group || "", m.label || "", this.labels?.[m.page - 1] || m.page, MEASURE_KINDS[m.kind] || "Length", v.text, quantity, unit,
        m.kind === "area" ? v.sub.replace(/^perimeter /, "").replace(/ · .*$/, "") : ""]);
    }
    return rows;
  }

  /** Write the measurements into the PDF as measurement annotations (see
   *  markup.py): they show in any viewer and print. They leave the list. */
  async #measuresIntoPdf() {
    const tab = this.activeTab, items = tab?.measures || [];
    if (!items.length) return;
    const answer = await this.#dialog("Put the measurements in the PDF", [el("p", {},
      `${items.length === 1 ? "The measurement becomes" : `The ${items.length} measurements become`} part of the document: measurement `
      + "annotations with their values and names, which show in Acrobat and other viewers, print, and are listed under Comments. "
      + "They leave this list; Ctrl+Z on the page takes them out of the PDF again.")],
      [["cancel", "Cancel"], ["ok", "Put them in the PDF", true]]);
    if (answer !== "ok") return;
    const changes = items.map((m) => {
      const v = this.#measureValue(m), scale = this.#pageScale(m.page);
      return { op: "add", page: m.page, kind: "measure", shape: m.kind, points: m.pts, holes: m.holes || [],
        label: m.label || "", group: m.group || "", value: v.text, sub: m.kind === "area" ? v.sub : "",
        color: this.#groupColor(m.group) || "#1f4fd8",
        scale: scale ? { ratio: scale.ratio, units: scale.units, label: scale.label } : null };
    });
    const version = tab.version || 0;
    await this.#applyMarkup(changes, "Could not put the measurements in the PDF: ");
    if ((tab.version || 0) === version) return;  // it didn't work (and said so)
    this.#rememberMeasures();
    tab.measures = [];
    this.selected = null;
    this.#drawAllMeasures();
    this.toast(`${items.length === 1 ? "The measurement is" : `${items.length} measurements are`} in the PDF now. Save to keep them.`, 4000);
  }

  async #exportMeasures() {
    const items = this.activeTab?.measures || [];
    if (!items.length) return;
    const cell = (c) => /[",\n]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : String(c);
    const text = "﻿" + this.#measureRows().map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";  // (the BOM: Excel reads it as UTF-8)
    const stem = this.name.replace(/\.pdf$/i, "");
    await this.#saveBytes(new TextEncoder().encode(text), `${stem} measurements.csv`, () => {}, this.activeTab?.fileId);
  }

  #deleteMeasure(id) {
    const tab = this.activeTab;
    const m = tab?.measures?.find((x) => x.id === id);
    if (!m) return;
    this.#rememberMeasures();
    tab.measures = tab.measures.filter((x) => x !== m);
    if (this.selected?.id === id) this.selected = null;
    if (this.hot === id) this.hot = null;
    this.#drawMeasures(m.page);
    this.#renderMeasPanel();
  }

  #hotMeasure(id) {
    const pages = new Set();
    for (const m of this.activeTab?.measures || []) if (m.id === id || m.id === this.hot) pages.add(m.page);
    this.hot = id;
    for (const n of pages) this.#drawMeasures(n);
    for (const row of this.measPanel.querySelectorAll(".rd-mrow")) row.classList.remove("hot");
  }

  /** Scroll to a measurement and make it stand out for a moment. */
  #showMeasure(m) {
    const xs = m.pts.map((p) => p[0]), ys = m.pts.map((p) => p[1]);
    this.pdfViewer.scrollPageIntoView({ pageNumber: m.page, destArray: [null, { name: "XYZ" }, Math.min(...xs) - 60, Math.max(...ys) + 60, null] });
    this.#hotMeasure(m.id);
    clearTimeout(this.hotTimer);
    this.hotTimer = setTimeout(() => { if (this.hot === m.id) this.#hotMeasure(null); }, 1500);
  }

  async #copyMeasures() {
    const items = this.activeTab?.measures || [];
    const lines = this.#measureRows().map((r) => r.join("\t"));
    for (const t of this.measPanel.querySelectorAll(".rd-mtotals > div")) lines.push(["", t.firstChild.textContent, "", "", t.lastChild.textContent].join("\t"));
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      this.toast(`Copied ${items.length} measurement${items.length === 1 ? "" : "s"}.`, 2000);
    } catch (e) {
      this.toast("Couldn't copy to the clipboard.");
    }
  }

  /** What there is to do with the open document, for the command palette:
   *  [{label, keys?, words?, run}]. */
  commands() {
    const open = { icon: "open", label: "Open a PDF", keys: "Ctrl+O", words: "file browse", run: () => this.chooseFile() };
    if (!this.doc) return [open];
    const v = this.pdfViewer;
    const tool = (label, mode, words = "", icon = "pen") => ({ icon, label, words: "annotate " + words, run: () => this.useTool(mode) });
    const list = [
      { icon: "edittext", label: "Edit text", keys: "E", words: "change words typo rewrite paragraph add text", run: () => this.useTool("EDITTEXT") },
      { icon: "search", label: "Find in document", keys: "Ctrl+F", words: "search look for", run: () => this.openFind() },
      tool("Highlight text", "HIGHLIGHT", "mark marker", "highlight"), tool("Add a text box", "FREETEXT", "note comment type", "text"),
      tool("Draw", "INK", "pen ink freehand", "draw"), tool("Add a picture", "STAMP", "image photo stamp", "image"),
      tool("Add a note", "NOTE", "comment sticky review", "note"), tool("Underline text", "UNDERLINE", "markup", "underline"),
      tool("Strike out text", "STRIKEOUT", "strikethrough delete markup", "strike"),
      tool("Draw a shape", "SHAPE", "rectangle ellipse circle cloud arrow line markup revision", "shapes"),
      tool("Stamp the page", "STAMPS", "approved draft confidential final received reviewed rubber stamp", "stamp"),
      tool("Edit pictures", "IMAGES", "image photo move resize replace delete logo", "imgedit"),
      tool("Edit links", "LINKS", "hyperlink url web address page add change remove", "link"),
      tool("Sign with a digital ID", "CERTSIGN", "certificate pfx p12 digital signature sign", "badge"),
      tool("Redact (black out for good)", "REDACT", "redaction black out remove hide censor private confidential", "redact"),
      { icon: "badge", label: "Check the signatures", words: "verify valid certificate signed", run: () => this.#signatureDetails() },
      { icon: "comments", label: "Comments", words: "notes replies review list markup", run: () => { this.#setSidebar(true); this.#showTab("comments"); } },
      { icon: "sign", label: "Sign", words: "signature autograph", run: () => this.sign() },
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
      { icon: "full", label: "Fit page", words: "zoom whole", run: () => { v.currentScaleValue = "page-fit"; } },
      { icon: "spread", label: "Fit width", words: "zoom", run: () => { v.currentScaleValue = "page-width"; } },
      { icon: "plus", label: "Actual size", words: "zoom 100%", run: () => { v.currentScaleValue = "page-actual"; } },
      { icon: "ruler", label: "True paper size", words: "zoom real scale blueprint plan drawing", run: () => this.#viewAt("true") },
      { icon: "ruler", label: "Measure a distance", keys: "M", words: "length dimension blueprint plan scale ruler snap", run: () => this.measureTool("distance") },
      { icon: "area", label: "Measure an area", words: "room square feet metres blueprint plan scale", run: () => this.measureTool("area") },
      { icon: "rect", label: "Measure a rectangle", words: "room area two corners blueprint takeoff", run: () => this.measureTool("rect") },
      { icon: "cutout", label: "Cut an opening out of an area", words: "subtract hole stair shaft column deduct takeoff", run: () => this.measureTool("cutout") },
      { icon: "count", label: "Count things", words: "outlets doors fixtures windows tally takeoff", run: () => this.measureTool("count") },
      { icon: "angle", label: "Measure an angle", words: "degrees corner slope", run: () => this.measureTool("angle") },
      { icon: "layers", label: this.overlay ? "Stop comparing revisions" : "Compare with another revision", words: "overlay difference drawing changes old new version",
        run: () => (this.overlay ? this.closeCompare() : this.compareRevision()) },
      { icon: "csv", label: "Save the measurements as CSV", words: "excel spreadsheet export takeoff", run: () => this.#exportMeasures() },
      { icon: "pdfin", label: "Put the measurements in the PDF", words: "annotations save keep markup", run: () => this.#measuresIntoPdf() },
      { icon: "path", label: "Measure a run of lines (path)", words: "polyline walls total length baseboard blueprint", run: () => this.measureTool("path") },
      { icon: "list", label: "List of measurements", words: "totals copy units blueprint takeoff", run: () => { this.toggleScaleBar(true); this.#toggleMeasPanel(true); } },
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
    const bytes = await this.#currentBytes();
    this.onEdit(toolId, new File([bytes], this.#copyName(), { type: "application/pdf" }), this.password);
  }
}
