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

import { BASE, DOC_OPTIONS, loadPdfjs as loadLibrary } from "./pdfjs.js";

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
  pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  pointer: '<path d="m4 4 7 17 2.5-7.5L21 11z"/>',
  highlight: '<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>',
  text: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
  draw: '<path d="M3 17c3-3 5-9 8-9s1 8 4 8 4-4 6-6"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  sign: '<path d="M3 17c2-1 3-5 5-5s0 5 2 5 3-6 5-6 1 6 3 6 2-2 3-2"/><path d="M3 21h18"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
};

// Annotation colours (highlights use the pale versions, as on paper).
const COLORS = [["Black", "#000000", "#bdbdbd"], ["Blue", "#1f4fd8", "#80ebff"], ["Red", "#d52b1e", "#ff4f5f"],
  ["Green", "#1f8a3b", "#53ffbc"], ["Yellow", "#b8860b", "#ffff98"]];
const HIGHLIGHT_COLORS = "yellow=#FFFF98,green=#53FFBC,blue=#80EBFF,pink=#FFCBE6,red=#FF4F5F";
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

const ZOOM_PRESETS = [
  ["auto", "Automatic"], ["page-fit", "Fit page"], ["page-width", "Fit width"], ["page-actual", "Actual size"],
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
const isTyping = (e) => /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) || e.target.isContentEditable;
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ } },
};

export class Reader {
  constructor(host, { token, isDesktop, api, tools = [], onEdit } = {}) {
    this.host = host;
    this.token = token;
    this.isDesktop = isDesktop || (() => false);
    this.api = api || (() => null);
    this.tools = tools;
    this.onEdit = onEdit;
    this.doc = null;
    this.bytes = null;       // original file bytes
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
    this.openBtn = b("open", "Open a PDF (Ctrl+O)");
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
    this.rotCcwBtn = b("ccw", "Rotate counter-clockwise", { disabled: true, class: "rd-btn rd-hide-narrow" });
    this.rotCwBtn = b("cw", "Rotate clockwise (R)", { disabled: true, class: "rd-btn rd-hide-narrow" });
    this.findBtn = b("search", "Find in document (Ctrl+F)", { disabled: true, "aria-pressed": "false" });
    this.annotateBtn = b("pen", "Annotate and sign", { disabled: true, "aria-pressed": "false" });
    this.moreBtn = b("more", "More", { "aria-haspopup": "menu", "aria-expanded": "false" });

    const toolbar = el("div", { class: "rd-toolbar", role: "toolbar", "aria-label": "Reader" },
      el("div", { class: "rd-group" }, this.sidebarBtn, this.openBtn),
      this.titleEl,
      el("div", { class: "rd-group" }, this.prevBtn, this.nextBtn,
        el("label", { class: "rd-page" }, this.pageInput, this.pageCount)),
      el("span", { class: "rd-sep" }),
      el("div", { class: "rd-group" }, this.zoomOutBtn, this.zoomSelect, this.zoomInBtn),
      el("span", { class: "rd-sep rd-hide-narrow" }),
      el("div", { class: "rd-group" }, this.rotCcwBtn, this.rotCwBtn, this.findBtn, this.annotateBtn, this.moreBtn));

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
    ];
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

    // Sidebar
    this.thumbsTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "true" }, "Pages");
    this.outlineTab = el("button", { class: "rd-tab", type: "button", role: "tab", "aria-selected": "false", disabled: true }, "Bookmarks");
    this.thumbsList = el("ol", { class: "rd-thumbs" });
    this.outlineList = el("ul", { class: "rd-outline" });
    this.thumbsPanel = el("div", { class: "rd-panel", role: "tabpanel" }, this.thumbsList);
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
        el("p", { class: "keys" }, "Ctrl+F find · Ctrl +/− zoom · R rotate · N / P next / previous page")));
    this.main = el("div", { class: "rd-main" }, this.container, this.loading, this.empty);
    this.body = el("div", { class: "rd-body" }, this.sidebar, this.main);

    this.menu = el("div", { class: "rd-menu", role: "menu", hidden: true });
    this.root.append(toolbar, this.findbar, this.annotbar, this.body, this.menu, this.fileInput);
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
      this.#menuItem("save", this.#changed() ? "Save a copy with your changes…" : "Save a copy…", () => this.saveCopy()),
      this.#menuItem("info", "Document properties", () => this.showProperties()),
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
    this.zoomSelect.addEventListener("change", () => { this.pdfViewer.currentScaleValue = this.zoomSelect.value; });
    this.rotCwBtn.addEventListener("click", () => this.rotate(90));
    this.rotCcwBtn.addEventListener("click", () => this.rotate(-90));
    this.findBtn.addEventListener("click", () => (this.findbar.hidden ? this.openFind() : this.closeFind()));
    this.moreBtn.addEventListener("click", () => this.#toggleMenu());
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
    this.undoBtn.addEventListener("click", () => this.uiManager?.undo());
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
      const file = [...e.dataTransfer.files].find((f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name));
      if (file) this.openFile(file); else this.toast("That isn't a PDF.");
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
      if (!this.container.classList.contains("hand") || e.button !== 0 || e.target.closest("a, input, textarea, select, button")) return;
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
    if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); this.pdfViewer.increaseScale(); return; }
    if (mod && e.key === "-") { e.preventDefault(); this.pdfViewer.decreaseScale(); return; }
    if (mod && e.key === "0") { e.preventDefault(); this.pdfViewer.currentScaleValue = "auto"; return; }
    if (mod || e.altKey || isTyping(e)) return;
    if (e.key === "Escape" && !this.findbar.hidden) { this.closeFind(); return; }
    if (e.key === "Escape" && this.editMode && this.editMode !== "NONE") { this.#setEditMode("NONE"); return; }
    const v = this.pdfViewer;
    switch (e.key) {
      case "n": case "j": v.nextPage(); break;
      case "p": case "k": v.previousPage(); break;
      case "r": this.rotate(90); break;
      case "R": this.rotate(-90); break;
      case "Home": v.currentPageNumber = 1; break;
      case "End": v.currentPageNumber = v.pagesCount; break;
      case "ArrowLeft": if (this.container.scrollWidth <= this.container.clientWidth) v.previousPage(); else return; break;
      case "ArrowRight": if (this.container.scrollWidth <= this.container.clientWidth) v.nextPage(); else return; break;
      default: return;
    }
    e.preventDefault();
  }

  // ---- visibility (called by the app when switching tools) --------------------
  show() { this.active = true; this.root.hidden = false; if (this.doc) this.container.focus({ preventScroll: true }); }
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
    this.fileInput.click();
  }

  async openById(id) {
    this.#busy(true);
    try {
      const res = await fetch("/api/file/" + encodeURIComponent(id), { headers: { "X-Pdfm-Token": this.token } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const name = res.headers.get("X-Pdfm-Filename") || "document.pdf";
      await this.openBytes(new Uint8Array(await res.arrayBuffer()), name);
    } catch (e) {
      this.#busy(false);
      this.toast("Could not open the file: " + (e.message || e));
    }
  }

  async openFile(file, password = null) {
    await this.openBytes(new Uint8Array(await file.arrayBuffer()), file.name || "document.pdf", password);
  }

  async openBytes(bytes, name, password = null) {
    if (this.#changed() && !confirm(`Your changes to “${this.name}” haven't been saved. Open the other file anyway?`)) return;
    this.#busy(true);
    try {
      await loadPdfjs();
      this.#ensureViewer();
      await this.#closeDocument();
      this.bytes = bytes;
      this.name = name;
      this.password = password;
      const task = pdfjs.getDocument({
        ...DOC_OPTIONS,
        data: bytes.slice(),  // pdf.js takes ownership of the buffer; keep our copy intact
        password: password || undefined,
        enableXfa: true,
      });
      task.onPassword = (update, reason) => {
        this.#busy(false);
        this.#askPassword(reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD).then((pw) => {
          if (pw === null) { task.destroy(); return; }
          this.password = pw;
          this.#busy(true);
          update(pw);
        });
      };
      this.task = task;
      const doc = await task.promise;
      this.doc = doc;
      this.pdfViewer.setDocument(doc);
      this.linkService.setDocument(doc, null);
      this.findController.setDocument(doc);
      this.titleEl.textContent = name;
      this.titleEl.title = name;
      document.title = name + " — PDF Manager";
      this.empty.hidden = true;
      for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
        this.zoomSelect, this.rotCcwBtn, this.rotCwBtn, this.findBtn, this.annotateBtn]) c.disabled = false;
      this.pageCount.textContent = `of ${doc.numPages}`;
      this.#setSidebar(store.get("pdfm-reader-sidebar", window.innerWidth > 760));
      this.#buildThumbs();
      this.#buildOutline();
      doc.getPageLabels().then((labels) => {
        if (doc !== this.doc || !labels) return;
        // Only use labels that differ from plain page numbers.
        if (labels.some((l, i) => l !== String(i + 1))) { this.labels = labels; this.pdfViewer.setPageLabels(labels); this.#updatePage(); }
      }).catch(() => {});
    } catch (e) {
      if (e && e.name === "PasswordException") return;  // cancelled at the password prompt
      await this.#closeDocument();
      this.empty.hidden = false;
      this.toast(e && e.name === "InvalidPDFException" ? `“${name}” isn't a valid PDF.` : "Could not open the PDF: " + (e.message || e));
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
      this.#updatePage();
      this.container.focus({ preventScroll: true });
    });
    eventBus.on("pagechanging", () => { this.#updatePage(); this.#remember(); });
    eventBus.on("scalechanging", ({ scale, presetValue }) => {
      const value = presetValue || String(scale);
      const preset = ZOOM_PRESETS.find(([v]) => v === value || (!presetValue && Math.abs(parseFloat(v) - scale) < 0.001));
      if (preset) { this.zoomSelect.value = preset[0]; this.zoomCustom.hidden = true; }
      else { this.zoomCustom.textContent = Math.round(scale * 100) + "%"; this.zoomCustom.hidden = false; this.zoomSelect.value = "custom"; }
      this.#remember();
    });
    eventBus.on("rotationchanging", () => this.#remember());
    eventBus.on("updatefindmatchescount", ({ matchesCount }) => this.#findResult(null, matchesCount));
    eventBus.on("updatefindcontrolstate", ({ state, matchesCount }) => this.#findResult(state, matchesCount));
  }

  async #closeDocument() {
    this.closeFind();
    this.closeAnnotate();
    this.uiManager = null;
    this.labels = null;
    if (this.thumbObserver) { this.thumbObserver.disconnect(); this.thumbObserver = null; }
    this.thumbsList.replaceChildren();
    this.outlineList.replaceChildren();
    if (this.doc) {
      this.pdfViewer.setDocument(null);
      this.linkService.setDocument(null);
      this.findController.setDocument(null);
      const doc = this.doc;
      this.doc = null;
      // pdf.js frees a document through the task that loaded it.
      await (doc.loadingTask || this.task).destroy();
    }
    this.task = null;
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
      const li = el("li", { class: "rd-thumb", "data-page": n, tabindex: "0", role: "button", "aria-label": `Page ${n}` }, box, el("span", {}, String(n)));
      li.addEventListener("click", () => { this.pdfViewer.currentPageNumber = n; });
      li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.pdfViewer.currentPageNumber = n; } });
      items.push(li);
    }
    this.thumbsList.replaceChildren(...items);
    for (const li of items) this.thumbObserver.observe(li);
    this.#updatePage();
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

  /** Switch the pdf.js editing tool: NONE (select), HIGHLIGHT, FREETEXT, INK or STAMP. */
  #setEditMode(name, create = false) {
    if (!this.pdfViewer || !this.doc) return;
    const mode = pdfjs.AnnotationEditorType[name];
    if (mode === undefined) return;
    if (this.annotbar.hidden && name !== "NONE") this.openAnnotate();
    try {
      this.pdfViewer.annotationEditorMode = { mode };
    } catch (e) { this.toast("Annotating isn't available for this document."); return; }
    this.#showEditMode(name);
    // "Picture" asks for an image file straight away.
    if (create && this.uiManager) setTimeout(() => this.uiManager.updateParams(pdfjs.AnnotationEditorParamsType.CREATE, null), 50);
  }

  #showEditMode(name) {
    this.editMode = name;
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

  // ---- saving, printing, editing ------------------------------------------------
  /** Filled-in form fields or annotations that aren't in the file yet. */
  #changed() { return !!(this.doc && this.doc.annotationStorage.size > 0); }

  async #currentBytes() {
    if (!this.#changed()) return this.bytes;
    this.uiManager?.commitOrRemove?.();  // finish the annotation being edited
    return await this.doc.saveDocument();
  }

  #copyName() {
    const stem = this.name.replace(/\.pdf$/i, "");
    return this.#changed() ? `${stem} (edited).pdf` : `${stem}.pdf`;
  }

  async saveCopy() {
    if (!this.doc) return;
    const bytes = await this.#currentBytes();
    const name = this.#copyName();
    const api = this.api();
    if (this.isDesktop() && api && api.save_result) {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), name);
      try {
        const res = await fetch("/api/keep", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
        const { id } = await res.json();
        const path = await api.save_result(id, name);
        if (path) this.toast("Saved to " + path, 5000, ["Show in folder", () => api.reveal(path)]);
      } catch (e) { this.toast("Could not save: " + (e.message || e)); }
      return;
    }
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const a = el("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
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

  async #edit(toolId) {
    if (!this.doc || !this.onEdit) return;
    const bytes = await this.#currentBytes();
    this.onEdit(toolId, new File([bytes], this.#copyName(), { type: "application/pdf" }), this.password);
  }
}
