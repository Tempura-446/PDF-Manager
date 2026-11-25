/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// The reader's navigation, sidebar (thumbnails and bookmarks), and page
// arrangement (context menu, rotate/delete/duplicate/move/insert/extract).
// A part of Reader (see ReaderNav below).

import * as files from "./files.js";
import { el, ICON, store, svg, viewer } from "./readerkit.js";

// Thumbnails (see #buildThumbs): the list's padding above its first row; what a
// row is taller than its picture (its padding, the gap and the page number:
// see .rd-thumb in reader.css); how many drawn thumbnails are kept.
const THUMB_PAD = 8, THUMB_EXTRA = 44, THUMBS_KEPT = 150;
const pagesText = (pages) => pages.length === 1 ? `Page ${pages[0]}` : `${pages.length} pages`;

/** Reader's navigation, sidebar and page tools, as a class it extends. */
export const ReaderNav = (Base) => class extends Base {
  // ---- navigation -----------------------------------------------------------
  _updatePage() {
    if (!this.doc) return;
    const v = this.pdfViewer, n = v.currentPageNumber || 1;
    const total = v.pagesCount || this.doc.numPages;  // before layout, pagesCount is 0
    this.pageInput.value = this.labels ? v.currentPageLabel || n : n;
    this.pageCount.textContent = this.labels ? `(${n} of ${total})` : `of ${total}`;
    const pct = Math.min(100, Math.round((n / Math.max(1, total)) * 100));
    this.pageCount.title = total > 1 ? `Reading progress: ${pct}% (page ${n} of ${total})` : "";
    this.prevBtn.disabled = n <= 1;
    this.nextBtn.disabled = n >= total;
    const t = this.thumbs;
    if (!t) return;
    if (t.current !== n) {
      t.rows.get(t.current)?.classList.remove("current");
      t.rows.get(n)?.classList.add("current");
      t.current = n;
    }
    this._revealThumb(n);
  }

  _goToTypedPage() {
    const v = this.pdfViewer, text = this.pageInput.value.trim();
    if (this.labels && this.labels.includes(text)) { v.currentPageLabel = text; return; }
    const n = parseInt(text, 10);
    if (n >= 1 && n <= v.pagesCount) v.currentPageNumber = n;
    else this._updatePage();
  }

  rotate(delta) {
    // Just after a tab was picked, the one before is still showing: turn the picked one once it's there.
    if (this.switching) { this._serial(() => this.rotate(delta)); return; }
    if (!this.doc) return;
    // (Pages not laid out yet: after they are, and after the turn remembered for the file.)
    if (!this.pdfViewer.pagesCount) { const doc = this.doc; this.eventBus.on("pagesinit", () => { if (this.doc === doc) this.rotate(delta); }, { once: true }); return; }
    this.pdfViewer.pagesRotation = (this.pdfViewer.pagesRotation + delta + 360) % 360;
    this._rebuildThumbsSoon();
  }

  toggleSpread() {
    const { SpreadMode } = viewer;
    this.pdfViewer.spreadMode = this.pdfViewer.spreadMode === SpreadMode.NONE ? SpreadMode.ODD : SpreadMode.NONE;
  }
  toggleHand() { this.container.classList.toggle("hand"); }
  pageTheme() {
    const saved = store.get("pdfm-reader-theme", null);
    if (saved && ["light", "night", "sepia", "eink"].includes(saved)) return saved;
    return store.get("pdfm-reader-dark", false) ? "night" : "light";
  }
  setPageTheme(theme = "light") {
    const valid = ["light", "night", "sepia", "eink"].includes(theme) ? theme : "light";
    for (const box of [this.container, this.splitContainer]) {
      if (!box) continue;
      if (valid === "light") box.removeAttribute("data-page-theme");
      else box.setAttribute("data-page-theme", valid);
      box.classList.toggle("dark", valid === "night");
    }
    store.set("pdfm-reader-theme", valid);
    store.set("pdfm-reader-dark", valid === "night");
  }
  toggleDarkPages() {
    this.setPageTheme(this.pageTheme() === "night" ? "light" : "night");
  }
  toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (this.root.requestFullscreen) this.root.requestFullscreen().catch(() => this.toast("Full screen isn't available here."));
    else this.toast("Full screen isn't available here.");
  }


  // ---- sidebar: thumbnails and bookmarks ----------------------------------------
  toggleSidebar() { this._setSidebar(this.sidebar.hidden); store.set("pdfm-reader-sidebar", !this.sidebar.hidden); }
  _setSidebar(open) {
    this.sidebar.hidden = !open;
    this.sidebarBtn.setAttribute("aria-pressed", String(open));
    if (open) { this._updatePage(); this._showThumbs(); }
  }
  _showTab(which) {
    for (const [name, tab, panel] of [["thumbs", this.thumbsTab, this.thumbsPanel], ["outline", this.outlineTab, this.outlinePanel],
      ["comments", this.commentsTab, this.commentsPanel], ["layers", this.layersTab, this.layersPanel],
      ["fields", this.fieldsTab, this.fieldsPanel]]) {
      tab.setAttribute("aria-selected", String(name === which));
      panel.hidden = name !== which;
    }
    if (which === "comments") this._listComments();
    if (which === "thumbs") this._showThumbs();
  }

  // Thumbnails are made when the Pages panel first shows them, and only the
  // rows near what the panel shows exist: a document of 3000 pages costs what
  // one of 30 does. Every page still has its place in the list (worked out
  // from the pages' sizes), so the list scrolls as if all of them were there.
  _thumbsVisible() { return !!this.doc && !this.sidebar.hidden && !this.thumbsPanel.hidden && this.pdfViewer?.pagesCount > 0; }

  /** The Pages panel is shown (or its document just got its pages): make the
   *  thumbnails if they aren't there yet. */
  _showThumbs() {
    if (!this._thumbsVisible()) return;
    if (!this.thumbs || this.thumbs.doc !== this.doc) this._buildThumbs();
    else this._layoutThumbs();
    this._revealThumb();
  }

  _buildThumbs() {
    const doc = this.doc, count = doc.numPages;
    this.thumbsList.replaceChildren();
    // heights[n]: how tall page n's row is; tops[n]: where it starts in the list.
    this.thumbs = { doc, count, heights: new Float64Array(count + 2), tops: new Float64Array(count + 2), rows: new Map(),
      canvases: new Map(), queue: [], busy: false, held: null, current: this.pdfViewer.currentPageNumber || 1 };
    this._sizeThumbs();
  }

  /** Every row's height from its page's size (as far as the viewer knows the
   *  sizes: all of them once "pagesloaded"), then the rows in their places. */
  _sizeThumbs() {
    const t = this.thumbs;
    if (!t || t.doc !== this.doc) return;
    for (let n = 1; n <= t.count; n++) {
      const vp = this.pdfViewer.getPageView(n - 1)?.viewport;
      t.heights[n] = Math.round(128 * (vp ? vp.height / vp.width : 1.4142)) + THUMB_EXTRA;
    }
    this._placeThumbs();
    this._layoutThumbs();
  }

  _placeThumbs() {
    const t = this.thumbs;
    for (let n = 1; n <= t.count; n++) t.tops[n + 1] = t.tops[n] + t.heights[n];
    this.thumbsList.style.height = t.tops[t.count + 1] + "px";
    for (const [n, li] of t.rows) li.style.top = THUMB_PAD + t.tops[n] + "px";
  }

  /** Where the list starts in what its panel scrolls. */
  _thumbsTop() {
    const panel = this.thumbsPanel;
    return this.thumbsList.getBoundingClientRect().top - panel.getBoundingClientRect().top + panel.scrollTop;
  }

  /** The rows the panel shows now (and a screenful around them) exist; the others don't. */
  _layoutThumbs() {
    const t = this.thumbs;
    if (!t || !this._thumbsVisible()) return;
    const panel = this.thumbsPanel, around = Math.max(400, panel.clientHeight);
    const from = panel.scrollTop - this._thumbsTop() - THUMB_PAD - around, to = from + panel.clientHeight + 2 * around;
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
    for (let n = first; n <= last; n++) if (!t.rows.has(n)) this._addThumb(n);
    if (t.queue.length && !t.busy) this._drainThumbs();
  }

  _addThumb(n) {
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
  _revealThumb(n = this.pdfViewer?.currentPageNumber || 1) {
    const t = this.thumbs;
    if (!t || !this._thumbsVisible() || n < 1 || n > t.count) return;
    const panel = this.thumbsPanel, top = this._thumbsTop() + THUMB_PAD + t.tops[n], bottom = top + t.heights[n];
    if (top < panel.scrollTop || bottom > panel.scrollTop + panel.clientHeight) panel.scrollTop = top - panel.clientHeight / 3;
    this._layoutThumbs();
  }

  _focusThumb(n) {
    const t = this.thumbs;
    if (!t || n < 1 || n > t.count) return;
    this._revealThumb(n);
    t.rows.get(n)?.focus();
  }

  // ---- thumbnails: pick, drag to move, drop a PDF to insert ---------------------------------
  _select(pages, anchor = pages.at(-1)) {
    this.pickedPages = new Set(pages);
    this.selectAnchor = anchor;
    for (const [n, li] of this.thumbs?.rows || []) li.setAttribute("aria-selected", String(this.pickedPages.has(n)));
    this._renderPageBar();
  }

  _renderPageBar() {
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
        b("x", "", () => this._select([]), "Clear the selection"));
    } else {
      const after = pages[0] || this.pdfViewer?.currentPageNumber || 0;
      this.pageBar.replaceChildren(b("plus", "Insert PDF", () => this.insertPdf(after), "Insert a PDF's pages after the selected page"),
        el("span", { class: "rd-pagebar-hint" }, "Drag to reorder"));
    }
  }

  _wireThumbs() {
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
        this._select(Array.from({ length: z - a + 1 }, (_, i) => a + i), this.selectAnchor);
      } else if (e.ctrlKey || e.metaKey) {
        const next = new Set(this.pickedPages);
        if (next.has(n)) next.delete(n); else next.add(n);
        this._select([...next], n);
      } else {
        this._select([n]);
        this.pdfViewer.currentPageNumber = n;
      }
    });
    list.addEventListener("keydown", (e) => {
      const n = pageOf(e);
      if (!n) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this._select([n]); this.pdfViewer.currentPageNumber = n; }
      if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); this.deletePages(this.pickedPages.has(n) ? [...this.pickedPages] : [n]); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        this._focusThumb(n + (e.key === "ArrowDown" ? 1 : -1));
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
      if (!this.pickedPages.has(n)) this._select([n]);
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
    const release = () => { if (this.thumbs?.held) { this.thumbs.held = null; this._layoutThumbs(); } };
    list.addEventListener("dragend", () => { clear(); release(); });
    this.thumbsPanel.addEventListener("scroll", () => this._layoutThumbs(), { passive: true });
    new ResizeObserver(() => this._layoutThumbs()).observe(this.thumbsPanel);
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
  async _drainThumbs() {
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
        page.cleanup();
        if (t !== this.thumbs || doc !== this.doc) { canvas.width = 0; canvas.height = 0; break; }
        // Drawn thumbnails are kept for when their row comes back; not all of a long document's.
        t.canvases.set(n, canvas);
        for (const old of t.canvases.keys()) {
          if (t.canvases.size <= THUMBS_KEPT) break;
          if (!t.rows.has(old)) {
            const c = t.canvases.get(old);
            if (c) { c.width = 0; c.height = 0; }
            t.canvases.delete(old);
          }
        }
        const height = Math.round(128 * viewport.height / viewport.width);
        const box = t.rows.get(n)?.firstChild;
        if (box) { box.style.height = height + "px"; box.replaceChildren(canvas); }
        if (height + THUMB_EXTRA !== t.heights[n]) {  // not the size its place was kept for: the rows after it move
          const grew = height + THUMB_EXTRA - t.heights[n], panel = this.thumbsPanel;
          const above = this._thumbsTop() + THUMB_PAD + t.tops[n] < panel.scrollTop;
          t.heights[n] = height + THUMB_EXTRA;
          this._placeThumbs();
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
    for (const c of t.canvases.values()) { c.width = 0; c.height = 0; }
    t.canvases.clear();
    for (const n of t.rows.keys()) if (!t.queue.includes(n)) t.queue.push(n);
    if (!t.busy) this._drainThumbs();
  }

  /** Show the sidebar's tab ``which`` ("thumbs", "outline", "comments", "layers"). */
  _showSidebarTab(which) { this._showTab(which); }

  _rebuildThumbsSoon() {
    clearTimeout(this.thumbTimer);
    this.thumbTimer = setTimeout(() => {
      if (!this.doc) return;
      this.thumbs = null;
      this.thumbsList.replaceChildren();
      this.pickedPages = new Set();
      this._renderPageBar();
      this._showThumbs();
    }, 250);
  }

  async _buildOutline() {
    const doc = this.doc;
    const outline = await doc.getOutline().catch(() => null);
    if (doc !== this.doc) return;
    this.outlineTab.disabled = !outline || !outline.length;
    if (!outline || !outline.length) {
      this.outlineList.replaceChildren();
      this.outlinePanel.replaceChildren(el("p", { class: "rd-empty-note" }, "This document has no bookmarks."));
      if (!this.outlinePanel.hidden) this._showTab("thumbs");
      return;
    }
    this.outlinePanel.replaceChildren(this.outlineList);
    const build = (items, depth) => items.map((item) => {
      const li = el("li", { class: [item.bold ? "bold" : "", item.italic ? "italic" : ""].join(" ").trim() || null });
      const link = el("a", { href: "#", title: item.title }, item.title || "(untitled)");
      link.addEventListener("click", (e) => {
        e.preventDefault();
        if (item.dest) this.linkService.goToDestination(item.dest);
        else if (item.url) this._openLink(item.url);
        if (window.innerWidth <= 760) this._setSidebar(false);
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
    if (doc.numPages >= 20 && this.commentsPanel.hidden) this._showTab("outline");
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

  // ---- the page menu (right-click) ---------------------------------------------------------
  _pageMenu(e) {
    if (!this.doc || e.target.closest?.(".rd-textedit-box, input, textarea")) return;
    const thumb = e.target.closest?.(".rd-thumb");
    const pageEl = e.target.closest?.(".page");
    const page = Number(thumb?.dataset.page || pageEl?.dataset.pageNumber);
    if (!page) return;
    e.preventDefault();
    // On a thumbnail, the menu acts on the selected pages if it's one of them.
    const pages = thumb && this.pickedPages.has(page) ? [...this.pickedPages].sort((a, b) => a - b) : [page];
    if (thumb && !this.pickedPages.has(page)) this._select([page]);
    const what = pagesText(pages).replace(/^Page \d+$/, "this page");
    const item = (icon, label, run, keys = "") => {
      const b = el("button", { type: "button", role: "menuitem" });
      b.innerHTML = svg(icon);
      b.append(el("span", {}, label));
      if (keys) b.append(el("kbd", {}, keys));
      b.addEventListener("click", () => { this._closeContextMenu(); run(); });
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
  _closeContextMenu() { if (!this.ctxMenu.hidden) this.ctxMenu.hidden = true; }

  // ---- pages: turn, delete, copy, move, insert, extract -------------------------------------
  _pagePlan(count = this.doc.numPages) { return Array.from({ length: count }, (_, i) => ({ page: i + 1 })); }

  /** ``plan``: the pages' new order, or a function making it for the
   *  document shown when the change's turn comes (a change asked for while
   *  another tab is still being shown waits for it, and is that tab's). A
   *  plan made before then is for the document shown when it was asked:
   *  if another one is shown by its turn, nothing is changed. */
  _arrange(plan, { inserts = [], message = "", page = null } = {}) {
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
      return this._pagePlan(count).map((e) => (pages.includes(e.page) ? { ...e, rotate: delta } : e));
    };
    return this._arrange(plan, { page: pages[0], message: `${pagesText(pages)} turned` });
  }
  deletePages(pages) {
    if (!this.doc || !pages.length) return;
    if (pages.length >= this.doc.numPages) { this.toast("A PDF needs at least one page: keep one, or close the document."); return; }
    const plan = this._pagePlan().filter((e) => !pages.includes(e.page));
    return this._arrange(plan, { page: Math.min(Math.min(...pages), plan.length), message: `${pagesText(pages)} deleted` });
  }
  duplicatePages(pages) {
    if (!this.doc || !pages.length) return;
    const last = Math.max(...pages), plan = this._pagePlan();
    plan.splice(last, 0, ...[...pages].sort((a, b) => a - b).map((page) => ({ page })));
    return this._arrange(plan, { page: last + 1, message: `${pagesText(pages)} copied` });
  }
  /** Move ``pages`` so they come before page ``before`` (pages + 1 = the end). */
  movePages(pages, before) {
    if (!this.doc || !pages.length) return;
    const moving = [...pages].sort((a, b) => a - b).map((page) => ({ page }));
    const rest = this._pagePlan().filter((e) => !pages.includes(e.page));
    let at = rest.findIndex((e) => e.page >= before);
    if (at < 0) at = rest.length;
    rest.splice(at, 0, ...moving);
    if (rest.every((e, i) => e.page === i + 1)) return;  // nothing moved
    return this._arrange(rest, { page: at + 1, message: `${pagesText(pages)} moved` });
  }
  /** Put a PDF's pages after page ``after`` (0 = at the start). */
  async insertPdf(after, file = null) {
    if (!this.doc) return;
    file = file || await this._pickPdf();
    if (!file) return;
    const plan = this._pagePlan();
    plan.splice(after, 0, { file: 1 });
    return this._arrange(plan, { inserts: [file], page: after + 1, message: `${file.name} inserted` });
  }
  _pickPdf() {
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
    this._markFlush();   // (a markup's arrow presses not written yet go in first)
    await this.chain;
    if (!this.doc || !pages.length) return;
    const tab = this.activeTab;
    this._busy(true);
    try {
      const bytes = await this._currentBytes();
      const plan = [...pages].sort((a, b) => a - b).map((page) => ({ page }));
      const res = await this._post("/api/pages/arrange", bytes, tab, { plan: JSON.stringify(plan) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const out = new Uint8Array(await res.arrayBuffer());
      const stem = this.name.replace(/\.pdf$/i, "");
      const label = pages.length === 1 ? `page ${pages[0]}` : `${pages.length} pages`;
      this._busy(false);
      await this._saveBytes(out, `${stem} (${label}).pdf`);
    } catch (e) {
      this.toast("Could not extract the pages: " + (e.message || e));
    } finally {
      this._busy(false);
    }
  }


  goToPage(n) { if (this.doc && n >= 1 && n <= this.pdfViewer.pagesCount) this.pdfViewer.currentPageNumber = n; }
  showBookmarks() { this._setSidebar(true); this._showTab("outline"); }
};
