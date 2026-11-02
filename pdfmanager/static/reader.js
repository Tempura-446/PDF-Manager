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
  save: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  tool: '<path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-3-3 8-8-1.3-1.3a4 4 0 0 0-5-5z"/><path d="m4 20 4-4"/>',
  twisty: '<path d="m9 6 6 6-6 6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
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
  trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
};

// Annotation colours (highlights use the pale versions, as on paper).
const COLORS = [["Black", "#000000", "#bdbdbd"], ["Blue", "#1f4fd8", "#80ebff"], ["Red", "#d52b1e", "#ff4f5f"],
  ["Green", "#1f8a3b", "#53ffbc"], ["Yellow", "#b8860b", "#ffff98"]];
const HIGHLIGHT_COLORS = "yellow=#FFFF98,green=#53FFBC,blue=#80EBFF,pink=#FFCBE6,red=#FF4F5F";
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

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
  constructor(host, { token, isDesktop, api, tools = [], onEdit, onAllTools, onCommands } = {}) {
    this.host = host;
    this.token = token;
    this.isDesktop = isDesktop || (() => false);
    this.api = api || (() => null);
    this.tools = tools;
    this.onEdit = onEdit;
    this.onAllTools = onAllTools;
    this.onCommands = onCommands;
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
    this.zoomSelect = el("select", { class: "rd-zoom", "aria-label": "Zoom", disabled: true });
    for (const [v, label] of ZOOM_PRESETS) this.zoomSelect.append(el("option", { value: v }, label));
    this.zoomCustom = el("option", { value: "custom", hidden: true, disabled: true });
    this.zoomSelect.append(this.zoomCustom);
    this.scaleBtn = el("button", { class: "rd-btn rd-scale-btn", type: "button", disabled: true, "aria-expanded": "false",
      title: "Drawing scale, true-size zoom and measuring (M to measure)", html: svg("ruler") + "<span>Scale</span>" });
    this.scaleLabel = this.scaleBtn.querySelector("span");
    this.toolsBtn = el("button", { class: "rd-btn rd-tools-btn", type: "button", title: "Show the tools (T)", "aria-expanded": "false",
      html: svg("sliders") + "<span>Tools</span>" + svg("down") });

    // The toolbar keeps what reading needs; everything else waits in the
    // tray under it until Tools pulls it out.
    const toolbar = el("div", { class: "rd-toolbar", role: "toolbar", "aria-label": "Reader" },
      el("div", { class: "rd-group" }, this.sidebarBtn),
      this.titleEl,
      el("div", { class: "rd-group" }, this.prevBtn, this.nextBtn,
        el("label", { class: "rd-page" }, this.pageInput, this.pageCount)),
      el("span", { class: "rd-sep" }),
      el("div", { class: "rd-group" }, this.zoomOutBtn, this.zoomSelect, this.zoomInBtn),
      el("span", { class: "rd-sep" }),
      this.scaleBtn, this.toolsBtn);

    // Tools tray
    const t = (name, label, title, attrs = {}) => el("button", { class: "rd-btn rd-tray-btn", type: "button", title, html: svg(name) + `<span>${label}</span>`, ...attrs });
    this.openBtn = t("open", "Open", "Open a PDF (Ctrl+O)");
    this.findBtn = t("search", "Find", "Find in document (Ctrl+F)", { "aria-pressed": "false" });
    this.annotateBtn = t("pen", "Annotate & sign", "Annotate and sign", { "aria-pressed": "false" });
    this.rotCcwBtn = b("ccw", "Rotate counter-clockwise (Shift+R)");
    this.rotCwBtn = b("cw", "Rotate clockwise (R)");
    this.printBtn = t("print", "Print", "Print (Ctrl+P)");
    this.saveBtn = t("save", "Save a copy", "Save a copy (Ctrl+S)");
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
      tool("pointer", "Select", "NONE"), tool("highlight", "Highlight", "HIGHLIGHT"), tool("text", "Text", "FREETEXT"),
      tool("draw", "Draw", "INK"), tool("image", "Picture", "STAMP"),
      tool("edittext", "Edit text", "EDITTEXT"),
    ];
    this.annotTools.at(-1).title = "Change words on the page: click a line of text (E)";
    this.signBtn = el("button", { class: "rd-btn rd-tool", type: "button", title: "Add your signature", html: svg("sign") + "<span>Sign</span>" });
    this.swatches = COLORS.map(([name, ink, mark], i) => el("button", { class: "rd-swatch", type: "button", title: name, "aria-label": name, "aria-pressed": String(i === 1),
      style: `--ink:${ink};--mark:${mark}`, "data-index": i }));
    this.sizeSelect = el("select", { class: "rd-zoom", "aria-label": "Line thickness or text size" });
    this.undoBtn = b("undo", "Undo (Ctrl+Z)", { disabled: true });
    this.redoBtn = b("redo", "Redo (Ctrl+Y)", { disabled: true });
    this.deleteBtn = b("trash", "Delete the selected annotation (Delete)", { disabled: true });
    this.annotSaveBtn = el("button", { class: "rd-btn primary", type: "button", title: "Save a copy with your changes (Ctrl+S)", html: svg("save") + "<span>Save a copy</span>" });
    this.annotCloseBtn = b("x", "Close the annotation tools");
    this.annotbar = el("div", { class: "rd-findbar rd-annotbar", hidden: true, role: "toolbar", "aria-label": "Annotate" },
      ...this.annotTools, this.signBtn, el("span", { class: "rd-sep" }),
      el("div", { class: "rd-swatches", role: "group", "aria-label": "Colour" }, ...this.swatches), this.sizeSelect,
      el("span", { class: "rd-sep" }), this.undoBtn, this.redoBtn, this.deleteBtn,
      el("span", { style: "flex:1" }), this.annotSaveBtn, this.annotCloseBtn);

    this.#buildScaleBar();

    // Sidebar
    this.thumbsTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "true" }, "Pages");
    this.outlineTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "false", disabled: true }, "Bookmarks");
    this.thumbsList = el("ol", { class: "rd-thumbs", role: "listbox", "aria-multiselectable": "true", "aria-label": "Pages" });
    this.selected = new Set();
    this.outlineList = el("ul", { class: "rd-outline" });
    this.pageBar = el("div", { class: "rd-pagebar" });
    this.thumbsPanel = el("div", { class: "rd-panel", role: "tabpanel" }, this.pageBar, this.thumbsList);
    this.outlinePanel = el("div", { class: "rd-panel", role: "tabpanel", hidden: true }, this.outlineList);
    this.sidebar = el("aside", { class: "rd-sidebar", hidden: true, "aria-label": "Pages and bookmarks" },
      el("div", { class: "rd-tabs", role: "tablist" }, this.thumbsTab, this.outlineTab), this.thumbsPanel, this.outlinePanel);

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
    this.main = el("div", { class: "rd-main" }, this.container, this.loading, this.empty);
    this.body = el("div", { class: "rd-body" }, this.sidebar, this.main);

    this.menu = el("div", { class: "rd-menu", role: "menu", hidden: true });
    this.ctxMenu = el("div", { class: "rd-menu rd-ctxmenu", role: "menu", hidden: true, "aria-label": "Page" });
    this.tabbar = el("div", { class: "rd-tabbar", role: "tablist", "aria-label": "Open documents", hidden: true });
    this.root.append(toolbar, this.tray, this.tabbar, this.findbar, this.annotbar, this.scalebar, this.body, this.menu, this.ctxMenu, this.fileInput);
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
      this.#menuItem("save", this.#edited() ? "Save a copy with your changes…" : "Save a copy…", () => this.saveCopy()),
      this.#menuItem("info", "Document properties", () => this.showProperties()),
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
    this.zoomSelect.addEventListener("change", () => {
      if (this.zoomSelect.value === "true-size") this.#viewAt("true");
      else this.pdfViewer.currentScaleValue = this.zoomSelect.value;
    });
    this.scaleBtn.addEventListener("click", () => this.toggleScaleBar());
    this.rotCwBtn.addEventListener("click", () => this.rotate(90));
    this.rotCcwBtn.addEventListener("click", () => this.rotate(-90));
    this.findBtn.addEventListener("click", () => (this.findbar.hidden ? this.openFind() : this.closeFind()));
    this.moreBtn.addEventListener("click", () => this.#toggleMenu());
    this.toolsBtn.addEventListener("click", () => this.toggleTray());
    this.printBtn.addEventListener("click", () => this.print());
    this.saveBtn.addEventListener("click", () => this.saveCopy());
    if (this.appToolsBtn) this.appToolsBtn.addEventListener("click", () => this.onAllTools());
    if (this.commandsBtn) this.commandsBtn.addEventListener("click", () => this.onCommands());
    document.addEventListener("pointerdown", (e) => { if (!this.menu.hidden && !this.menu.contains(e.target) && e.target !== this.moreBtn && !this.moreBtn.contains(e.target)) this.#closeMenu(); });
    this.thumbsTab.addEventListener("click", () => this.#showTab("thumbs"));
    this.outlineTab.addEventListener("click", () => this.#showTab("outline"));

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
    this.undoBtn.addEventListener("click", () => (this.textEditMode ? this.#undoTextEdit() : this.uiManager?.undo()));
    this.redoBtn.addEventListener("click", () => this.uiManager?.redo());
    this.deleteBtn.addEventListener("click", () => this.uiManager?.delete());
    this.annotSaveBtn.addEventListener("click", () => this.saveCopy());
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

    // Edit text tool: click a line of text to change it.
    this.container.addEventListener("click", (e) => this.#textClick(e), true);

    document.addEventListener("keydown", (e) => this.#onKey(e));
  }

  #onKey(e) {
    if (!this.active || document.querySelector("dialog[open]")) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "o") { e.preventDefault(); this.chooseFile(); return; }
    if (e.key === "Escape" && !this.menu.hidden) { this.#closeMenu(); this.moreBtn.focus(); return; }
    if (!this.doc) return;
    if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); this.openFind(); return; }
    if (mod && e.key.toLowerCase() === "p") { e.preventDefault(); this.print(); return; }
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); this.saveCopy(); return; }
    if (mod && e.key.toLowerCase() === "z" && !e.shiftKey && !isTyping(e) && this.activeTab?.undo?.length
        && (this.textEditMode || this.annotbar.hidden)) { e.preventDefault(); this.#undoTextEdit(); return; }
    if (e.key === "Escape" && !this.ctxMenu.hidden) { this.#closeContextMenu(); return; }
    if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); this.pdfViewer.increaseScale(); return; }
    if (mod && e.key === "-") { e.preventDefault(); this.pdfViewer.decreaseScale(); return; }
    if (mod && e.key === "0") { e.preventDefault(); this.pdfViewer.currentScaleValue = "auto"; return; }
    if (mod || e.altKey || isTyping(e)) return;
    if (this.measureMode && this.#measureKey(e)) { e.preventDefault(); return; }
    if (e.key === "Escape" && !this.findbar.hidden) { this.closeFind(); return; }
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
      const res = await fetch("/api/file/" + encodeURIComponent(id), { headers: { "X-Pdfm-Token": this.token } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const name = decodeURIComponent(res.headers.get("X-Pdfm-Filename") || "") || "document.pdf";
      await this.openBytes(new Uint8Array(await res.arrayBuffer()), name, null, goto);
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
  openBytes(bytes, name, password = null, goto = null) {
    return this.#serial(() => this.#openBytesNow(bytes, name, password, goto));
  }

  async #openBytesNow(bytes, name, password, goto) {
    const same = this.tabs.find((t) => t.name === name && sameBytes(t.original, bytes));
    if (same) {
      if (same === this.activeTab) { if (goto) this.#goto(goto); }
      else { if (goto) same.goto = goto; await this.#switchToNow(same); }  // applied once its pages are there
      return;
    }
    const previous = this.activeTab;
    await this.#stash();
    // edited: bytes holds changes made here; dirty: changes not saved to a file
    // yet; version: counts every change, so a save knows what it included.
    const tab = { id: ++tabSeq, original: bytes, bytes, name, password, edited: false, dirty: false, version: 0, goto };
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
      this.zoomSelect, this.rotCcwBtn, this.rotCwBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = true;
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
        data: tab.bytes.slice(),  // pdf.js takes ownership of the buffer; keep our copy intact
        password: tab.password || undefined,
        enableXfa: true,
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
      // (The viewer hands the document to the find controller itself once the
      // first page is drawn; doing it here too would reset a search started
      // when the pages appear, e.g. from the folder search.)
      this.titleEl.textContent = tab.name;
      this.titleEl.title = tab.name;
      document.title = tab.name + " — ZPDF";
      this.empty.hidden = true;
      for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
        this.zoomSelect, this.rotCcwBtn, this.rotCwBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = false;
      this.pageCount.textContent = `of ${doc.numPages}`;
      this.root.classList.add("reading");
      this.toggleTray(store.get("pdfm-reader-tray", false), false);
      this.#setSidebar(store.get("pdfm-reader-sidebar", false));
      this.#buildThumbs();
      this.#buildOutline();
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
    this.pdfViewer = new viewer.PDFViewer({
      container: this.container, viewer: this.viewerEl, eventBus,
      linkService: this.linkService, findController: this.findController,
      annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS,
      annotationEditorMode: pdfjs.AnnotationEditorType.NONE,
      annotationEditorHighlightColors: HIGHLIGHT_COLORS,
      imageResourcesPath: BASE + "web/images/",
    });
    this.linkService.setViewer(this.pdfViewer);
    eventBus.on("annotationeditoruimanager", ({ uiManager }) => { this.uiManager = uiManager; this.#applyAnnotParams(); });
    // pdf.js asks to switch tools itself, e.g. after a picture is pasted.
    eventBus.on("switchannotationeditormode", ({ mode }) => this.#setEditMode(this.#modeName(mode)));
    eventBus.on("annotationeditormodechanged", ({ mode }) => this.#showEditMode(this.#modeName(mode)));
    eventBus.on("editingstateschanged", ({ details }) => {
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
    eventBus.on("pagerendered", ({ pageNumber }) => this.#drawMeasures(pageNumber));
    eventBus.on("scalechanging", ({ scale, presetValue }) => {
      const value = presetValue || String(scale);
      const preset = ZOOM_PRESETS.find(([v]) => v === value || (!presetValue && Math.abs(parseFloat(v) - scale) < 0.001));
      if (!presetValue && Math.abs(scale - this.#trueZoom()) < 0.001) { this.zoomSelect.value = "true-size"; this.zoomCustom.hidden = true; }
      else if (preset) { this.zoomSelect.value = preset[0]; this.zoomCustom.hidden = true; }
      else { this.zoomCustom.textContent = Math.round(scale * 100) + "%"; this.zoomCustom.hidden = false; this.zoomSelect.value = "custom"; }
      if (this.viewAtZoom && Math.abs(scale - this.viewAtZoom) > 0.001) { this.viewAtZoom = null; this.viewAtSelect.value = ""; }
      this.#remember();
      requestAnimationFrame(() => this.#drawAllMeasures());
    });
    eventBus.on("rotationchanging", () => { this.#remember(); requestAnimationFrame(() => this.#drawAllMeasures()); });
    eventBus.on("updatefindmatchescount", ({ matchesCount }) => this.#findResult(null, matchesCount));
    eventBus.on("updatefindcontrolstate", ({ state, matchesCount }) => this.#findResult(state, matchesCount));
  }

  async #closeDocument() {
    this.closeFind();
    this.closeAnnotate();
    this.measureTool(null);
    this.uiManager = null;
    this.labels = null;
    if (this.thumbObserver) { this.thumbObserver.disconnect(); this.thumbObserver = null; }
    this.thumbsList.replaceChildren();
    this.outlineList.replaceChildren();
    const doc = this.doc, task = this.task;
    this.doc = null;
    this.task = null;
    if (doc) {
      this.pdfViewer.setDocument(null);
      this.linkService.setDocument(null);
      this.findController.setDocument(null);
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
    const thumbs = which === "thumbs";
    this.thumbsTab.setAttribute("aria-selected", String(thumbs));
    this.outlineTab.setAttribute("aria-selected", String(!thumbs));
    this.thumbsPanel.hidden = !thumbs;
    this.outlinePanel.hidden = thumbs;
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
      this.#showTab("thumbs");
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
    if (doc.numPages >= 20) this.#showTab("outline");
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
    this.#showEditMode(this.editMode || "NONE");
  }
  closeAnnotate() {
    if (this.annotbar.hidden) return;
    this.annotbar.hidden = true;
    this.annotateBtn.setAttribute("aria-pressed", "false");
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
      this.toast("Click a line to change it, Shift+click for its whole paragraph, or click an empty spot to add words. Old words are covered, not removed — to remove text for good, use Redact.", 8000);
    }
    if (name !== "NONE" || editText) this.measureTool(null);
    this.textEditMode = editText;
    this.root.classList.toggle("rd-edittext", editText);
    if (editText) name = "NONE";
    const mode = pdfjs.AnnotationEditorType[name];
    if (mode === undefined) return;
    if (this.annotbar.hidden && (name !== "NONE" || editText)) this.openAnnotate();
    try {
      if (!(editText && this.pdfViewer.annotationEditorMode === mode)) this.pdfViewer.annotationEditorMode = { mode };
    } catch (e) {
      if (!editText) { this.toast("Annotating isn't available for this document."); return; }
    }
    this.#showEditMode(name);
    // "Picture" asks for an image file straight away.
    if (create && this.uiManager) setTimeout(() => this.uiManager.updateParams(pdfjs.AnnotationEditorParamsType.CREATE, null), 50);
  }

  #showEditMode(name) {
    if (name === "NONE" && this.textEditMode) name = "EDITTEXT";
    this.editMode = name;
    if (name === "EDITTEXT") this.undoBtn.disabled = !this.activeTab?.undo?.length;
    for (const btn of this.annotTools) btn.setAttribute("aria-pressed", String(btn.dataset.mode === name));
    const sizes = name === "FREETEXT" ? [[10, "10 pt"], [12, "12 pt"], [14, "14 pt"], [18, "18 pt"], [24, "24 pt"], [36, "36 pt"]]
      : name === "INK" ? [[1, "Thin"], [2, "Fine"], [4, "Medium"], [8, "Thick"], [14, "Marker"]]
      : name === "HIGHLIGHT" ? [[8, "Thin"], [12, "Normal"], [20, "Wide"]] : [];
    const key = { FREETEXT: "text", INK: "ink", HIGHLIGHT: "mark" }[name];
    const chosen = this.annotParams?.size?.[key];
    this.sizeSelect.replaceChildren(...sizes.map(([v, label]) => el("option", { value: v, selected: v === chosen }, label)));
    this.sizeSelect.hidden = !sizes.length;
    this.root.querySelector(".rd-swatches").hidden = !key;
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

  #setSize(value, key = { FREETEXT: "text", INK: "ink", HIGHLIGHT: "mark" }[this.editMode], remember = true) {
    if (!key || !value) return;
    const P = pdfjs.AnnotationEditorParamsType;
    const type = { text: P.FREETEXT_SIZE, ink: P.INK_THICKNESS, mark: P.HIGHLIGHT_THICKNESS }[key];
    if (this.uiManager) this.uiManager.updateParams(type, value);
    if (remember) { this.annotParams.size[key] = value; store.set("pdfm-annot", this.annotParams); }
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
    const rows = [
      ["File", this.name], ["File size", size(this.bytes.length)],
      ["Title", metadata?.get("dc:title") || info.Title], ["Author", info.Author], ["Subject", info.Subject],
      ["Keywords", info.Keywords], ["Created", date(info.CreationDate)], ["Modified", date(info.ModDate)],
      ["Application", info.Creator], ["PDF producer", info.Producer], ["PDF version", info.PDFFormatVersion],
      ["Pages", String(this.doc.numPages)],
      ["Page size", `${(w / 72 * 25.4).toFixed(0)} × ${(h / 72 * 25.4).toFixed(0)} mm (${(w / 72).toFixed(2)} × ${(h / 72).toFixed(2)} in)`],
      ["Fast web view", info.IsLinearized ? "Yes" : "No"],
      ["Form", info.IsAcroFormPresent ? "Yes" : info.IsXFAPresent ? "Yes (XFA)" : "No"],
      ["Password protected", this.password ? "Yes" : "No"],
    ].filter(([, v]) => v);
    const dl = el("dl", { class: "rd-props" });
    for (const [k, v] of rows) dl.append(el("dt", {}, k), el("dd", {}, v));
    await this.#dialog("Document properties", [dl], [["ok", "Close", true]]);
  }

  toast(message, ms = 4000, action = null) {
    if (!message) return;
    if (this.toastEl) this.toastEl.remove();
    const t = el("div", { class: "rd-toast", role: "status" }, el("span", {}, message));
    if (action) t.append(el("button", { type: "button", onclick: action[1] }, action[0]));
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
    if (span && !span.classList.contains("markedContent") && span.textContent.trim()) {
      this.#openTextEditor(span, pageEl, pageNumber, e.shiftKey ? "paragraph" : "line");
    } else {
      this.#openNewText(e, pageEl, pageNumber);  // an empty spot: new words there
    }
  }

  /** The lines of text on a page as drawn: [{spans, left, top, right, bottom, h}],
   *  top to bottom. Words next to each other share a line; a far-off column doesn't. */
  #pageLines(pageEl) {
    const items = [...pageEl.querySelectorAll(".textLayer span")]
      .filter((s) => !s.classList.contains("markedContent") && s.textContent.trim())
      .map((s) => ({ s, r: s.getBoundingClientRect() }))
      .sort((a, b) => (a.r.top + a.r.bottom) / 2 - (b.r.top + b.r.bottom) / 2 || a.r.left - b.r.left);
    const lines = [];
    for (const it of items) {
      const h = it.r.height, cy = (it.r.top + it.r.bottom) / 2;
      const line = lines.find((l) => Math.abs((l.top + l.bottom) / 2 - cy) < Math.min(l.h, h) * 0.5
        && it.r.left - l.right < Math.max(l.h, h) * 2.5 && l.left - it.r.right < Math.max(l.h, h) * 2.5);
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
    const close = (a, b) => {
      const gap = b.top - a.bottom, h = Math.min(a.h, b.h);
      return gap > -h * 0.3 && gap < h * 1.1 && Math.abs(a.h - b.h) < h * 0.35
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
    };
  }

  /** A box on the page for typing: [wrapper, input or textarea]. */
  #editorBox(pageEl, { left, top, width, height, fontSize, fontFamily, multiline, value, label, hint }) {
    const page = pageEl.getBoundingClientRect();
    const field = multiline
      ? el("textarea", { class: "rd-textedit", spellcheck: "true", "aria-label": label })
      : el("input", { type: "text", class: "rd-textedit", spellcheck: "true", "aria-label": label });
    field.value = value;
    Object.assign(field.style, { width: `${width}px`, height: `${height}px`, fontSize: `${fontSize}px`, fontFamily: fontFamily || "" });
    const box = el("div", { class: "rd-textedit-box" }, field, el("div", { class: "rd-textedit-hint" }, ...hint));
    Object.assign(box.style, { left: `${left - page.left - pageEl.clientLeft - 3}px`, top: `${top - page.top - pageEl.clientTop - 2}px` });
    return [box, field];
  }

  #openTextEditor(span, pageEl, pageNumber, mode = "line") {
    this.#closeTextEditor(false);
    const viewport = this.pdfViewer.getPageView(pageNumber - 1).viewport;
    const layer = pageEl.querySelector(".textLayer").getBoundingClientRect();
    const sideways = viewport.rotation % 180 !== 0;
    const paragraph = mode === "paragraph" && !sideways ? this.#paragraphOf(span, pageEl) : null;
    const r = span.getBoundingClientRect();
    const area = paragraph || { left: r.left, right: r.right, top: r.top, bottom: r.bottom, h: sideways ? r.width : r.height };
    const original = paragraph ? paragraph.text : span.textContent;
    // With the view turned, the line runs up or down the screen: size the box
    // from the line's own length and height.
    const lineW = sideways ? r.height : area.right - area.left, lineH = area.h;
    const toggle = el("button", { type: "button", class: "rd-textedit-mode", title: paragraph ? "Change this line only" : "Rewrite the whole paragraph (or Shift+click a line)" },
      paragraph ? "One line" : "¶ Whole paragraph");
    toggle.disabled = sideways;
    const [box, field] = this.#editorBox(pageEl, {
      left: area.left, top: area.top, multiline: !!paragraph, value: original,
      width: Math.max(lineW + (paragraph ? 16 : 40), 80),
      height: paragraph ? area.bottom - area.top + lineH * 1.5 : lineH + 4,
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
    this.#startEditing(box, field, pageEl, original, (text) =>
      this.#applyTextEdit({ page: pageNumber, rect, text, ...(paragraph ? { paragraph: true } : {}) }));
  }

  /** New words where the page was clicked; they take the look of the nearest text. */
  #openNewText(e, pageEl, pageNumber) {
    this.#closeTextEditor(true);
    const viewport = this.pdfViewer.getPageView(pageNumber - 1).viewport;
    const layer = pageEl.querySelector(".textLayer")?.getBoundingClientRect() || pageEl.getBoundingClientRect();
    let near = null, best = Infinity;
    for (const line of this.#pageLines(pageEl)) {
      const d = Math.hypot(Math.max(line.left - e.clientX, 0, e.clientX - line.right), Math.max(line.top - e.clientY, 0, e.clientY - line.bottom) * 2);
      if (d < best) { best = d; near = line; }
    }
    const h = near ? near.h : 16 * viewport.scale;
    const [box, field] = this.#editorBox(pageEl, {
      left: e.clientX, top: e.clientY, width: 240, height: h + 4, fontSize: Math.max(8, h * 0.8),
      fontFamily: near ? getComputedStyle(near.spans[0].s).fontFamily : "", value: "",
      label: "New text (Enter to add, Esc to cancel)",
      hint: [el("span", {}, "New text · Enter to add · Esc to cancel")],
    });
    const [x, y] = viewport.convertToPdfPoint(e.clientX - layer.left, e.clientY - layer.top);
    this.#startEditing(box, field, pageEl, "", (text) => this.#applyTextEdit({ page: pageNumber, at: [x, y], text }));
  }

  #startEditing(box, field, pageEl, original, apply) {
    let done = false;
    const finish = (keep) => {
      if (done) return;
      done = true;
      box.remove();
      if (this.textEditor?.box === box) this.textEditor = null;
      if (keep && field.value !== original && (original || field.value.trim())) apply(field.value);
    };
    field.addEventListener("keydown", (e) => {
      e.stopPropagation();
      const multiline = field.tagName === "TEXTAREA";
      if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(true); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    field.addEventListener("blur", () => setTimeout(() => { if (!box.contains(document.activeElement)) finish(true); }));
    pageEl.append(box);
    this.textEditor = { box, finish };
    field.focus();
    field.select();
  }

  #closeTextEditor(keep) { this.textEditor?.finish(keep); }

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
  #changeDocument(make, { failure = "Could not change the document: ", goto = null, after = null } = {}) {
    return this.#serial(async () => {
      const tab = this.activeTab;
      if (!tab || !this.doc) return;
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
        const other = used.find((u) => u.kind !== "same");
        if (other) {
          this.toast(other.kind === "installed"
            ? `This PDF's copy of the font lacks some of those letters, so ${other.font} from this computer was used.`
            : `This PDF's copy of the font lacks some of those letters, so a similar font (${other.font}) was used.`, 7000);
        }
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
    const editing = this.textEditMode;
    await this.#load(tab);
    this.#renderTabs();
    if (tab !== this.activeTab) return;
    if (editing) {
      this.openAnnotate();
      this.#setEditMode("EDITTEXT");
    }
    this.undoBtn.disabled = !tab.undo?.length;
  }

  // ---- saving, printing, editing ------------------------------------------------
  /** Filled-in form fields or annotations that aren't in the file yet. */
  #changed() { return !!(this.doc && this.doc.annotationStorage.size > 0); }
  #edited() { return this.#changed() || !!this.activeTab?.edited; }

  async #currentBytes() {
    this.#finishEditing();
    if (!this.#changed()) return this.bytes;
    return await this.doc.saveDocument();
  }

  #copyName() {
    const stem = this.name.replace(/\.pdf$/i, "");
    return this.#edited() ? `${stem} (edited).pdf` : `${stem}.pdf`;
  }

  async saveCopy() {
    await this.chain;  // a text change still on its way is included
    if (!this.doc) return;
    const tab = this.activeTab, version = tab.version || 0;
    const bytes = await this.#currentBytes();
    await this.#saveBytes(bytes, this.#copyName(), () => this.#markSaved(tab, version));
  }

  /** Save ``bytes`` as a file the person picks (or download it). */
  async #saveBytes(bytes, name, saved = () => {}) {
    const api = this.api();
    if (this.isDesktop() && api && api.save_result) {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), name);
      try {
        const res = await fetch("/api/keep", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
        const { id } = await res.json();
        const path = await api.save_result(id, name);
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
    try {
      for (let n = 1; n <= total; n++) {
        this.toast(`Preparing page ${n} of ${total} for printing…`, 60000);
        const page = await doc.getPage(n);
        const viewport = page.getViewport({ scale: 150 / 72 });  // 150 dpi
        const canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
        await page.render({ canvas, viewport, intent: "print", annotationMode: pdfjs.AnnotationMode.ENABLE_STORAGE,
          printAnnotationStorage: doc.annotationStorage.print }).promise;
        const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
        box.append(el("img", { src: URL.createObjectURL(blob), alt: "" }));
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
      title: "Click both ends of a line whose real length you know, such as a dimension", html: svg("ruler") + "<span>Set from a dimension</span>" });
    this.viewAtSelect = el("select", { class: "rd-zoom", "aria-label": "Show the drawing at" });
    this.distanceBtn = el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": "false",
      title: "Measure a length: click both ends (M). Clicks snap to the drawing's lines (Alt: no snapping); hold Shift to keep it straight.", html: svg("ruler") + "<span>Distance</span>" });
    this.areaBtn = el("button", { class: "rd-btn rd-tool", type: "button", "aria-pressed": "false",
      title: "Measure an area: click its corners, then click the first one again (or press Enter)", html: svg("area") + "<span>Area</span>" });
    this.clearMeasuresBtn = el("button", { class: "rd-btn", type: "button", title: "Remove the measurements", html: svg("trash") + "<span>Clear</span>" });
    this.calibrateBtn = el("button", { class: "rd-btn", type: "button", title: "Match the app to your screen, so true paper size really is",
      html: svg("screen") + "<span>Calibrate screen</span>" });
    const close = el("button", { class: "rd-btn", type: "button", title: "Close the scale bar", "aria-label": "Close the scale bar", html: svg("x") });
    this.scalebar = el("div", { class: "rd-findbar rd-scalebar", hidden: true, role: "toolbar", "aria-label": "Scale and measuring" },
      el("label", {}, "Sheet scale", this.sheetScaleSelect), this.fromDimensionBtn, el("span", { class: "rd-sep" }),
      el("label", {}, "Show at", this.viewAtSelect), el("span", { class: "rd-sep" }),
      this.distanceBtn, this.areaBtn, this.clearMeasuresBtn,
      el("span", { style: "flex:1" }), this.calibrateBtn, close);
    this.sheetScaleSelect.addEventListener("change", () => this.#pickSheetScale(this.sheetScaleSelect.value));
    this.fromDimensionBtn.addEventListener("click", () => this.measureTool(this.measureMode === "calibrate" ? null : "calibrate"));
    this.viewAtSelect.addEventListener("change", () => { if (this.viewAtSelect.value) this.#viewAt(this.viewAtSelect.value); });
    this.distanceBtn.addEventListener("click", () => this.measureTool(this.measureMode === "distance" ? null : "distance"));
    this.areaBtn.addEventListener("click", () => this.measureTool(this.measureMode === "area" ? null : "area"));
    this.clearMeasuresBtn.addEventListener("click", () => this.clearMeasures());
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
    const n = this.pdfViewer.currentPageNumber || 1;
    if (!tab.detected.has(n)) {
      tab.detected.set(n, null);
      try {
        const page = await doc.getPage(n);
        const content = await page.getTextContent();
        const text = content.items.map((i) => (i.str || "") + (i.hasEOL ? "\n" : " ")).join("");
        tab.detected.set(n, measure.findScale(text));
      } catch (e) { /* no text: no scale */ }
      if (doc !== this.doc) return;
    }
    this.#showScale();
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
    this.sheetDetected.textContent = detected ? `${detected.label} (from the sheet)` : "";
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
    const keep = this.viewAtSelect.value;
    const options = [el("option", { value: "" }, "Choose…"), el("option", { value: "true" }, "True paper size")];
    if (scale) {
      const kind = (sc) => (sc.units === "m" ? "m" : sc.label.includes("= 1'-0") ? "arch" : "eng");
      const same = measure.ALL_SCALES.filter((sc) => (scale.custom ? sc.units === scale.units : kind(sc) === kind(scale)));
      options.push(el("optgroup", { label: "As if drawn at" }, ...same.map((sc) => el("option", { value: "s:" + measure.ALL_SCALES.indexOf(sc) }, sc.label))));
    }
    this.viewAtSelect.replaceChildren(...options);
    this.viewAtSelect.value = [...this.viewAtSelect.options].some((o) => o.value === keep) ? keep : "";
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
    if (!this.#screenPpi() && !(await this.calibrateScreen(true))) { this.#syncZoomSelect(); this.viewAtSelect.value = ""; return; }
    let zoom = this.#trueZoom(), what = "true paper size";
    if (value !== "true") {
      const target = measure.ALL_SCALES[+value.slice(2)];
      const scale = this.#pageScale(this.pdfViewer.currentPageNumber || 1);
      if (!target || !scale) { this.toast("Set this sheet's scale first."); return; }
      zoom *= scale.ratio / target.ratio;
      what = target.label;
    }
    const wanted = zoom;
    zoom = Math.min(10, Math.max(0.1, zoom));  // what the viewer can draw
    if (!Number.isFinite(zoom)) { this.toast("That zoom can't be worked out; calibrate the screen again."); return; }
    this.pdfViewer.currentScale = zoom;
    if (zoom !== wanted) this.toast(`That would be ${Math.round(wanted * 100)}%; the viewer stops at ${Math.round(zoom * 100)}%.`, 4000);
    this.viewAtZoom = zoom;
    this.viewAtSelect.value = value;
    this.toast(`Showing the drawing at ${what} on your screen`, 2500);
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
    for (let i = 0; i <= 6; i++) ruler.append(el("i", { style: `left:${(i / 6) * 100}%` }, el("b", {}, String(i))));
    ruler.append(el("span", {}, "6 inches"));
    const slider = el("input", { type: "range", min: "40", max: "320", step: "0.25", "aria-label": "Card size" });
    const diagonal = el("input", { type: "number", min: "5", max: "100", step: "0.1", placeholder: "e.g. 24", "aria-label": "Screen diagonal in inches" });
    const show = () => {
      card.style.width = `${CARD_W * cssPpi}px`;
      card.style.height = `${CARD_H * cssPpi}px`;
      ruler.style.width = `${6 * cssPpi}px`;
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
      el("p", {}, "Hold a ruler or a bank card against the screen and drag the slider until the 6 inches, or the card's outline, match exactly (a ruler is the more exact):"),
      el("div", { class: "rd-cardbox" }, ruler, card), slider,
      el("p", { class: "rd-or" }, "Or type your screen's size (its diagonal, as sold):"),
      el("label", { class: "rd-diag" }, diagonal, "inches"),
    ];
    const answer = await this.#dialog("Calibrate your screen", body, [["cancel", "Cancel"], ["ok", "Save", true]]);
    if (answer !== "ok" || !Number.isFinite(cssPpi)) return false;
    this.screenPpi = cssPpi * dpr;
    store.set("pdfm-screen-ppi", this.screenPpi);
    this.toast("Screen calibrated: true paper size now matches the real world.", 3000);
    if (this.zoomSelect.value === "true-size" && this.pdfViewer) this.pdfViewer.currentScale = this.#trueZoom();
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
    const page = this.draft?.page, hoverPage = this.hoverPage;
    this.measureMode = mode;
    this.draft = null;
    this.hover = null;
    this.hoverPage = null;
    if (page) this.#drawMeasures(page);
    if (hoverPage && hoverPage !== page) this.#drawMeasures(hoverPage);
    if (mode) this.#geometry(this.pdfViewer.currentPageNumber || 1);  // start reading the lines now
    this.root.classList.toggle("rd-measuring", !!mode);
    this.distanceBtn.setAttribute("aria-pressed", String(mode === "distance"));
    this.areaBtn.setAttribute("aria-pressed", String(mode === "area"));
    this.fromDimensionBtn.setAttribute("aria-pressed", String(mode === "calibrate"));
    if (mode === "calibrate") this.toast("Click both ends of a line whose real length you know, such as a dimension's extension lines. Clicks snap to the drawing's lines.", 6000);
    else if (mode && !this.#pageScale(this.pdfViewer.currentPageNumber || 1)) {
      this.toast("This sheet's scale isn't set, so lengths show as on paper. Pick it under Sheet scale, or use Set from a dimension.", 6000);
    } else if (mode && !this.activeTab.scaleChecked) {
      const n = this.pdfViewer.currentPageNumber || 1;
      if (this.#pageScale(n) === this.activeTab.detected?.get(n)) {
        this.activeTab.scaleChecked = true;
        this.toast(`The scale ${this.#pageScale(n).label} was read from the sheet. Measure one written dimension to check it before relying on it.`, 7000);
      }
    }
  }

  clearMeasures() {
    if (this.activeTab) this.activeTab.measures = [];
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
      if (this.draft?.page === n || this.hoverPage === n) this.#drawMeasures(n);
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
   *  measuring, the point snaps to the drawing's lines (Alt: no snapping);
   *  with Shift and a point already placed, it stays at a multiple of 45°
   *  from that point. ``snapped`` says what it landed on. */
  #pageAt(e, only = null) {
    const pageEl = e.target.closest?.(".page");
    const n = Number(pageEl?.dataset.pageNumber);
    if (!n || (only && n !== only)) return null;
    const view = this.pdfViewer.getPageView(n - 1);
    if (!view?.div || !view.viewport) return null;
    const box = view.div.getBoundingClientRect();
    const x = e.clientX - box.left - view.div.clientLeft, y = e.clientY - box.top - view.div.clientTop;
    let pdf = view.viewport.convertToPdfPoint(x, y);
    if (!Number.isFinite(pdf[0]) || !Number.isFinite(pdf[1])) return null;
    const pts = this.draft?.page === n ? this.draft.pts : null;
    const from = pts?.length ? pts.at(-1) : null;
    let ortho = null;
    if (e.shiftKey && from) {  // keep it straight
      ortho = Math.round(Math.atan2(pdf[1] - from[1], pdf[0] - from[0]) / (Math.PI / 4)) * (Math.PI / 4);
      const len = Math.hypot(pdf[0] - from[0], pdf[1] - from[1]);
      pdf = [from[0] + len * Math.cos(ortho), from[1] + len * Math.sin(ortho)];
    }
    let snapped = null;
    if (this.measureMode && !e.altKey) {
      try {
        const geometry = this.#geometry(n);
        const hit = geometry?.snap(pdf, SNAP_PX / view.viewport.scale, { from, ortho });
        if (hit && Number.isFinite(hit.x) && Number.isFinite(hit.y)) { pdf = [hit.x, hit.y]; snapped = hit.kind; }
      } catch (err) {
        // Snapping failing must never stop a measurement: the point stays where it was clicked.
        this.activeTab.geometry?.set(n, null);
      }
    }
    return { page: n, view, x, y, pdf, snapped };
  }

  #measureDown(e) {
    if (!this.measureMode || e.button !== 0) return;
    const at = this.#pageAt(e, this.draft?.page);
    if (!at) return;
    e.preventDefault();
    e.stopPropagation();
    if (!this.draft) {
      this.draft = { page: at.page, kind: this.measureMode, pts: [at.pdf], hover: null, snapped: at.snapped };
      this.#drawMeasures(at.page);
      return;
    }
    const d = this.draft;
    if (d.kind === "area") {
      const screen = (p) => at.view.viewport.convertToViewportPoint(...p);
      const [fx, fy] = screen(d.pts[0]), [lx, ly] = screen(d.pts.at(-1));
      const nearFirst = d.pts.length >= 3 && Math.hypot(at.x - fx, at.y - fy) < 9;
      const onLast = Math.hypot(at.x - lx, at.y - ly) < 4;  // the second click of a double-click
      if (nearFirst || (onLast && d.pts.length >= 3)) { this.#finishMeasure(); return; }
      if (!onLast) d.pts.push(at.pdf);
      this.#drawMeasures(at.page);
      return;
    }
    d.pts.push(at.pdf);
    this.#finishMeasure();
  }

  #measureMove(e) {
    if (!this.measureMode) return;
    if (!this.draft) {  // nothing placed yet: still show what a click would land on
      const at = this.#pageAt(e);
      const n = at?.page || this.hoverPage;
      this.hover = at?.snapped ? { page: at.page, pdf: at.pdf, snapped: at.snapped } : null;
      this.hoverPage = at?.page || null;
      if (n && !this.moveFrame) this.moveFrame = requestAnimationFrame(() => { this.moveFrame = null; this.#drawMeasures(n); if (n !== this.hoverPage && this.hoverPage) this.#drawMeasures(this.hoverPage); });
      return;
    }
    const at = this.#pageAt(e, this.draft.page);
    if (!at) return;
    this.draft.hover = at.pdf;
    this.draft.snapped = at.snapped;
    if (this.moveFrame) return;
    this.moveFrame = requestAnimationFrame(() => { this.moveFrame = null; if (this.draft) this.#drawMeasures(this.draft.page); });
  }

  async #finishMeasure() {
    const d = this.draft;
    this.draft = null;
    if (!d) return;
    if (d.kind === "calibrate") {
      this.#drawMeasures(d.page);
      const length = measure.distance(d.pts[0], d.pts[1]) * (this.pdfViewer.getPageView(d.page - 1).pdfPage?.userUnit || 1);
      if (length < 1) return;
      const units = this.#pageScale(d.page)?.units || "ft";
      const input = el("input", { type: "text", placeholder: units === "m" ? "e.g. 3.6 m or 3600 mm" : "e.g. 12'-6\" or 3.6 m", "aria-label": "Real length", autocomplete: "off" });
      const error = el("div", { class: "err", role: "alert", hidden: true }, "Type a length such as 12'-6\", 150\", 3.6 m or 3600 mm.");
      let real = null;
      for (;;) {
        const answer = await this.#dialog("How long is that line?", [el("p", {}, "Type its real length, as the drawing gives it:"), input, error],
          [["cancel", "Cancel"], ["ok", "Set the scale", true]]);
        if (answer !== "ok") { this.measureTool(null); return; }
        real = measure.parseLength(input.value, units);
        const ratio = real ? real.inches / (length / 72) : NaN;
        if (real && ratio >= 0.05 && ratio <= 20000) break;
        error.textContent = real ? `That would make the scale 1:${ratio < 1 ? ratio.toFixed(3) : Math.round(ratio)}, which doesn't look like a drawing scale. Check the length and the points you clicked.`
          : "Type a length such as 12'-6\", 150\", 3.6 m or 3600 mm.";
        error.hidden = false;
      }
      const scale = measure.scaleFrom(length, real);
      this.#setPageScale(d.page, scale);
      this.measureTool("distance");
      this.toast(`Scale set (${scale.label.replace(/^Set from a dimension \((.*)\)$/, "$1")}). Click two points to measure.`, 5000);
      return;
    }
    if (d.kind === "area" && d.pts.length < 3) { this.#drawMeasures(d.page); return; }
    const tab = this.activeTab;
    tab.measures ||= [];
    tab.measures.push({ page: d.page, kind: d.kind, pts: d.pts });
    this.#drawMeasures(d.page);
  }

  /** Esc, Enter and Backspace while measuring. True if the key was used. */
  #measureKey(e) {
    if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.key === "Escape") {
      if (this.draft) { const n = this.draft.page; this.draft = null; this.#drawMeasures(n); }
      else this.measureTool(null);
      return true;
    }
    if (e.key === "Enter" && this.draft?.kind === "area") { this.#finishMeasure(); return true; }
    if (e.key === "Backspace" || e.key === "Delete") {
      if (this.draft && this.draft.pts.length > 1) { this.draft.pts.pop(); this.#drawMeasures(this.draft.page); }
      else if (this.draft) { const n = this.draft.page; this.draft = null; this.#drawMeasures(n); }
      else if (this.activeTab?.measures?.length) { const m = this.activeTab.measures.pop(); this.#drawMeasures(m.page); }
      return true;
    }
    return false;
  }

  #drawAllMeasures() {
    if (!this.pdfViewer || !this.doc) return;
    const pages = new Set([...(this.activeTab?.measures || []).map((m) => m.page), ...(this.draft ? [this.draft.page] : [])]);
    for (const layer of this.viewerEl.querySelectorAll("svg.rd-measures")) pages.add(Number(layer.closest(".page")?.dataset.pageNumber));
    for (const n of pages) if (n) this.#drawMeasures(n);
  }

  /** Draw page ``n``'s measurements (and the one being made) over it. */
  #drawMeasures(n) {
    try { this.#drawMeasuresOn(n); } catch (e) { /* a bad point or scale: the page itself is unaffected */ }
  }

  #drawMeasuresOn(n) {
    if (!this.pdfViewer || !this.doc) return;
    const view = this.pdfViewer.getPageView(n - 1);
    if (!view?.div) return;
    const items = (this.activeTab?.measures || []).filter((m) => m.page === n);
    const d = this.draft?.page === n ? this.draft : null;
    const hover = !d && this.hover?.page === n ? this.hover : null;
    let layer = view.div.querySelector(":scope > svg.rd-measures");
    if (!items.length && !d && !hover) { layer?.remove(); return; }
    const NS = "http://www.w3.org/2000/svg";
    const make = (tag, attrs = {}) => { const node = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); return node; };
    if (!layer) { layer = make("svg", { class: "rd-measures", "aria-hidden": "true" }); view.div.append(layer); }
    layer.replaceChildren();
    const vp = view.viewport, scale = this.#pageScale(n);
    const uu = view.pdfPage?.userUnit || 1;  // a page can declare its unit bigger than a point
    const length = (pts) => measure.formatLength(pts * uu, scale);
    const area = (pts2) => measure.formatArea(pts2 * uu * uu, scale);
    const xy = (p) => vp.convertToViewportPoint(...p);
    const label = (x, y, lines, cls = "") => {
      const text = make("text", { x, y, class: "rd-measure-label" + cls, "text-anchor": "middle" });
      lines.forEach((line, i) => {
        const span = make("tspan", { x, dy: i ? "1.25em" : `${0.35 - (lines.length - 1) * 0.62}em` });
        span.textContent = line;
        text.append(span);
      });
      layer.append(text);
    };
    const dot = (x, y) => layer.append(make("circle", { cx: x, cy: y, r: 3.5, class: "rd-measure-dot" }));
    const drawLine = (a, b, draft, calibrate) => {
      const [ax, ay] = xy(a), [bx, by] = xy(b);
      layer.append(make("line", { x1: ax, y1: ay, x2: bx, y2: by, class: "rd-measure-line" + (draft ? " draft" : "") + (calibrate ? " calibrate" : "") }));
      dot(ax, ay); dot(bx, by);
      if (calibrate || Math.hypot(bx - ax, by - ay) < 2) return;
      // the label beside the line's middle, on its upper side
      let nx = -(by - ay), ny = bx - ax;
      const len = Math.hypot(nx, ny);
      nx /= len; ny /= len;
      if (ny > 0) { nx = -nx; ny = -ny; }
      label((ax + bx) / 2 + nx * 13, (ay + by) / 2 + ny * 13, [length(measure.distance(a, b))]);
    };
    const drawArea = (pts, draft) => {
      const screen = pts.map(xy);
      layer.append(make(draft ? "polyline" : "polygon", { points: screen.map((p) => p.join(",")).join(" "), class: "rd-measure-area" + (draft ? " draft" : "") }));
      for (const [x, y] of screen) dot(x, y);
      if (pts.length < 3) return;
      const cx = screen.reduce((s, p) => s + p[0], 0) / screen.length, cy = screen.reduce((s, p) => s + p[1], 0) / screen.length;
      label(cx, cy, [area(measure.polygonArea(pts)), "perimeter " + length(measure.perimeter(pts))], " area");
    };
    for (const m of items) (m.kind === "area" ? drawArea(m.pts) : drawLine(m.pts[0], m.pts[1]));
    if (d) {
      const pts = d.hover ? [...d.pts, d.hover] : d.pts;
      if (d.kind === "area") drawArea(pts, true);
      else if (pts.length > 1) drawLine(pts[0], pts[1], true, d.kind === "calibrate");
      else { const [x, y] = xy(pts[0]); dot(x, y); }
    }
    // What the next click would land on.
    const at = d?.hover && d.snapped ? { pdf: d.hover, snapped: d.snapped } : hover;
    if (at) {
      const [x, y] = xy(at.pdf), r = 7;
      const shapes = {
        end: make("rect", { x: x - r, y: y - r, width: 2 * r, height: 2 * r }),
        cross: make("path", { d: `M${x - r} ${y - r}L${x + r} ${y + r}M${x + r} ${y - r}L${x - r} ${y + r}` }),
        mid: make("path", { d: `M${x} ${y - r}L${x + r} ${y + r}L${x - r} ${y + r}Z` }),
        perp: make("path", { d: `M${x - r} ${y - r}V${y + r}H${x + r}M${x - r} ${y}H${x}V${y + r}` }),
        on: make("circle", { cx: x, cy: y, r }),
      };
      const mark = shapes[at.snapped];
      mark.setAttribute("class", "rd-snap");
      layer.append(mark);
      const words = { end: "end of line", cross: "crossing", mid: "middle", perp: "perpendicular", on: "on line" };
      const hint = make("text", { x: x + 12, y: y - 10, class: "rd-measure-label rd-snap-hint" });
      hint.textContent = words[at.snapped];
      layer.append(hint);
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
      { icon: "ruler", label: "Set the drawing scale from a dimension", words: "blueprint plan calibrate known length", run: () => this.measureTool("calibrate") },
      { icon: "ruler", label: "Drawing scale", words: "blueprint plan sheet show at measure", run: () => this.toggleScaleBar(true) },
      { icon: "screen", label: "Calibrate the screen for true size", words: "monitor dpi ppi real size card", run: () => this.calibrateScreen() },
      { icon: "plus", label: "Zoom in", keys: "Ctrl +", run: () => v.increaseScale() },
      { icon: "minus", label: "Zoom out", keys: "Ctrl −", run: () => v.decreaseScale() },
      { icon: "up", label: "First page", keys: "Home", words: "start beginning", run: () => this.goToPage(1) },
      { icon: "down", label: "Last page", keys: "End", words: "end", run: () => this.goToPage(v.pagesCount) },
      { icon: "spread", label: "Two-page view", words: "spread book", run: () => this.toggleSpread() },
      { icon: "moon", label: "Dark pages", words: "night invert", run: () => this.toggleDarkPages() },
      { icon: "hand", label: "Hand tool", words: "drag scroll pan", run: () => this.toggleHand() },
      { icon: "full", label: "Full screen", words: "presentation", run: () => this.toggleFullscreen() },
      { icon: "print", label: "Print", keys: "Ctrl+P", run: () => this.print() },
      { icon: "save", label: this.#edited() ? "Save a copy with your changes" : "Save a copy", keys: "Ctrl+S", words: "download export", run: () => this.saveCopy() },
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
