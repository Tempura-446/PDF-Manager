/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Side-by-side split view for the reader: shows either a second open tab or a
// second independent view of the current document beside the main pane, with a
// draggable divider, optional synchronized scroll & zoom, page-offset lock,
// and keyboard pane switching (Ctrl+\, F6 / Ctrl+Shift+\).
// A part of Reader (see SplitView below).

import { BASE, DOC_OPTIONS, destroyTask, openDocument } from "./pdfjs.js";
import { el, loadPdfjs, pdfjs, store, svg, viewer } from "./readerkit.js";

const MIN_RATIO = 0.2;
const MAX_RATIO = 0.8;

/** Reader's side-by-side split view, as a class it extends. */
export const SplitView = (Base) => class extends Base {
  /** Build the split bar, draggable divider, and secondary viewer pane (from #build). */
  _buildSplitView() {
    this.splitOpen = false;
    this.splitDoc = null;
    this.splitTask = null;
    this.splitViewer = null;
    this.splitEventBus = null;
    this.splitLinkService = null;
    this._splitRatio = 0.5;
    this._splitSyncing = false;
    this._splitSourceValue = "__same__";
    this._splitActivePane = "left";
    this._splitLoadId = 0;
    this._splitDiff = null;
    this._splitDiffIdx = -1;
    this._splitDiffOn = false;
    this._splitTrCache = new Map();
    this._splitTrReqId = 0;
    this._splitTrOrig = false;

    this.splitSource = el("select", { class: "rd-zoom rd-split-source", "aria-label": "Right pane document" });
    this.splitSync = el("input", { type: "checkbox", checked: true });
    this.splitOffset = el("input", {
      type: "number", class: "rd-overlay-offset rd-split-offset", value: "0", step: "1",
      "aria-label": "Split view page offset",
      title: "Right page = left page plus this offset",
    });
    this.splitLockOffsetBtn = el("button", {
      class: "rd-btn rd-split-lock-offset", type: "button",
      title: "Lock the current page difference between the two panes",
    }, "Lock offset");
    this.splitDiffBtn = el("button", {
      class: "rd-btn rd-split-diff-btn", type: "button", hidden: true,
      title: "Highlight text & visual differences between left and right documents",
    }, "Highlight diff");
    this.splitDiffPrev = el("button", {
      class: "rd-btn rd-split-diff-nav", type: "button", hidden: true,
      title: "Previous changed page", "aria-label": "Previous changed page", html: svg("up"),
    });
    this.splitDiffStatus = el("span", { class: "rd-split-diff-status", hidden: true, "aria-live": "polite" });
    this.splitDiffNext = el("button", {
      class: "rd-btn rd-split-diff-nav", type: "button", hidden: true,
      title: "Next changed page", "aria-label": "Next changed page", html: svg("down"),
    });
    this.splitSwitchBtn = el("button", {
      class: "rd-btn rd-split-switch", type: "button",
      title: "Switch active pane (F6)",
    }, "Switch pane");
    const splitClose = el("button", {
      class: "rd-btn rd-split-close", type: "button",
      title: "Close split view (Ctrl+\\)", "aria-label": "Close split view",
      html: svg("x"),
    });
    this.splitBar = el("div", {
      class: "rd-findbar rd-splitbar", hidden: true, role: "toolbar", "aria-label": "Split view",
    },
      el("span", { class: "rd-overlay-title" }, "Split view"),
      el("label", {}, "Right pane", this.splitSource),
      el("span", { class: "rd-sep" }),
      el("label", {}, this.splitSync, "Sync scroll"),
      el("label", {}, "Offset", this.splitOffset),
      this.splitLockOffsetBtn,
      this.splitDiffBtn, this.splitDiffPrev, this.splitDiffStatus, this.splitDiffNext,
      el("span", { class: "rd-sep" }),
      this.splitSwitchBtn,
      el("span", { style: "flex:1" }),
      splitClose);

    this.splitDivider = el("div", {
      class: "rd-split-divider", hidden: true, role: "separator",
      "aria-orientation": "vertical", "aria-label": "Resize split panes",
      "aria-valuemin": "20", "aria-valuemax": "80", "aria-valuenow": "50",
      tabindex: "0",
    });

    this.splitTitle = el("span", { class: "rd-split-title" });
    this.splitPrevBtn = el("button", { class: "rd-btn", type: "button", title: "Previous page (right pane)", "aria-label": "Previous page (right pane)", html: svg("up") });
    this.splitNextBtn = el("button", { class: "rd-btn", type: "button", title: "Next page (right pane)", "aria-label": "Next page (right pane)", html: svg("down") });
    this.splitPageStatus = el("span", { class: "rd-split-pages", "aria-live": "polite" });
    const splitHead = el("div", { class: "rd-split-head" },
      this.splitTitle,
      el("span", { style: "flex:1" }),
      this.splitPrevBtn, this.splitPageStatus, this.splitNextBtn);

    this.splitViewerEl = el("div", { class: "pdfViewer" });
    this.splitContainer = el("div", { class: "rd-container rd-split-container", tabindex: "0" }, this.splitViewerEl);

    // Live bilingual translation reading pane (shown when Right pane === "__translate__")
    this.splitTrFrom = el("select", { class: "rd-zoom", "aria-label": "Translate from" });
    this.splitTrTo = el("select", { class: "rd-zoom", "aria-label": "Translate to" });
    this.splitTrToggle = el("button", { class: "rd-btn", type: "button" }, "Show original");
    this.splitTrCopy = el("button", { class: "rd-btn", type: "button" }, "Copy");
    this.splitTrStatus = el("span", { class: "rd-split-tr-status", "aria-live": "polite" });
    this.splitTrBody = el("div", { class: "rd-split-tr-body", dir: "auto", tabindex: "0" });
    this.splitTranslatePane = el("div", { class: "rd-split-tr", hidden: true },
      el("div", { class: "rd-split-tr-bar" },
        this.splitTrFrom, el("span", {}, "→"), this.splitTrTo,
        this.splitTrToggle, this.splitTrCopy,
        el("span", { style: "flex:1" }), this.splitTrStatus),
      this.splitTrBody);

    this.splitLoading = el("div", { class: "rd-loading", hidden: true },
      el("div", { class: "rd-spinner", role: "progressbar", "aria-label": "Loading second view" }));
    this.splitPane = el("section", {
      class: "rd-split-pane", hidden: true, role: "region", "aria-label": "Second view",
    }, splitHead, el("div", { class: "rd-split-stage" }, this.splitContainer, this.splitTranslatePane, this.splitLoading));

    // Wire split bar controls
    this.splitSource.addEventListener("change", () => {
      this._splitSourceValue = this.splitSource.value;
      this._clearSplitDiff();
      if (this.splitOpen) this._loadSplitSource();
    });
    this.splitSync.addEventListener("change", () => {
      if (this.splitSync.checked && this.splitOpen) this._syncPane("left");
    });
    this.splitOffset.addEventListener("change", () => {
      this.splitOffset.value = String(Math.round(Number(this.splitOffset.value) || 0));
      if (this.splitSync.checked && this.splitOpen) this._syncPane("left");
    });
    this.splitLockOffsetBtn.addEventListener("click", () => this.lockSplitOffset());
    this.splitDiffBtn.addEventListener("click", () => this.toggleSplitDiff());
    this.splitDiffPrev.addEventListener("click", () => this._jumpSplitDiff(-1));
    this.splitDiffNext.addEventListener("click", () => this._jumpSplitDiff(1));
    this.splitSwitchBtn.addEventListener("click", () => this.switchSplitPane());
    splitClose.addEventListener("click", () => this.closeSplitView());
    this.splitPrevBtn.addEventListener("click", () => this.splitViewer?.previousPage());
    this.splitNextBtn.addEventListener("click", () => this.splitViewer?.nextPage());

    // Wire live bilingual translation controls
    const onLangChange = () => {
      store.set("pdfm-tr-from", this.splitTrFrom.value);
      store.set("pdfm-tr-to", this.splitTrTo.value);
      if (this.splitOpen && this._splitSourceValue === "__translate__") this._refreshSplitTranslation();
    };
    this.splitTrFrom.addEventListener("change", onLangChange);
    this.splitTrTo.addEventListener("change", onLangChange);
    this.splitTrToggle.addEventListener("click", () => {
      this._splitTrOrig = !this._splitTrOrig;
      this.splitTrToggle.textContent = this._splitTrOrig ? "Show translation" : "Show original";
      this._renderSplitTranslationText();
    });
    this.splitTrCopy.addEventListener("click", async () => {
      const txt = this.splitTrBody.textContent || "";
      if (txt && navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(txt); this.toast("Copied."); } catch (_) {}
      }
    });

    // Track active pane focus
    this.container.addEventListener("focus", () => this._setSplitActivePane("left"));
    this.container.addEventListener("pointerdown", () => this._setSplitActivePane("left"));
    this.splitContainer.addEventListener("focus", () => this._setSplitActivePane("right"));
    this.splitContainer.addEventListener("pointerdown", () => this._setSplitActivePane("right"));

    // Synchronized scroll listeners on both containers
    this.container.addEventListener("scroll", () => {
      if (this.splitOpen && this.splitSync.checked && !this._splitSyncing) this._syncPane("left");
    }, { passive: true });
    this.splitContainer.addEventListener("scroll", () => {
      if (this.splitOpen && this.splitSync.checked && !this._splitSyncing) this._syncPane("right");
    }, { passive: true });

    this._wireSplitDivider();
  }

  _wireSplitDivider() {
    let dragging = false;
    const applyFromClientX = (clientX) => {
      const leftRect = this.main.getBoundingClientRect();
      const rightRect = this.splitPane.getBoundingClientRect();
      const total = leftRect.width + rightRect.width;
      if (total <= 0) return;
      const ratio = Math.min(MAX_RATIO, Math.max(MIN_RATIO, (clientX - leftRect.left) / total));
      this._splitRatio = ratio;
      this._applySplitSizes();
    };
    this.splitDivider.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      dragging = true;
      this.splitDivider.classList.add("dragging");
      this.splitDivider.setPointerCapture?.(e.pointerId);
      e.preventDefault();
    });
    this.splitDivider.addEventListener("pointermove", (e) => {
      if (dragging) applyFromClientX(e.clientX);
    });
    const endDrag = (e) => {
      if (!dragging) return;
      dragging = false;
      this.splitDivider.classList.remove("dragging");
      if (e?.pointerId != null) this.splitDivider.releasePointerCapture?.(e.pointerId);
    };
    this.splitDivider.addEventListener("pointerup", endDrag);
    this.splitDivider.addEventListener("pointercancel", endDrag);
    this.splitDivider.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") {
        this._splitRatio = Math.max(MIN_RATIO, this._splitRatio - 0.05);
        this._applySplitSizes();
        e.preventDefault();
      } else if (e.key === "ArrowRight") {
        this._splitRatio = Math.min(MAX_RATIO, this._splitRatio + 0.05);
        this._applySplitSizes();
        e.preventDefault();
      }
    });
  }

  _applySplitSizes() {
    if (!this.splitOpen) {
      this.main.style.flex = "";
      this.splitPane.style.flex = "";
      return;
    }
    const pct = Math.round(this._splitRatio * 100);
    this.main.style.flex = `0 0 ${pct}%`;
    this.splitPane.style.flex = `1 1 ${100 - pct}%`;
    this.splitDivider.setAttribute("aria-valuenow", String(pct));
  }

  _setSplitActivePane(which) {
    this._splitActivePane = which === "right" ? "right" : "left";
    if (!this.splitOpen) {
      this.main.classList.remove("rd-pane-active");
      this.splitPane.classList.remove("rd-pane-active");
      return;
    }
    this.main.classList.toggle("rd-pane-active", this._splitActivePane === "left");
    this.splitPane.classList.toggle("rd-pane-active", this._splitActivePane === "right");
  }

  /** Switch keyboard focus between left and right panes (F6 / Ctrl+Shift+\). */
  switchSplitPane() {
    if (!this.splitOpen) return;
    const next = this._splitActivePane === "left" ? "right" : "left";
    this._setSplitActivePane(next);
    const target = next === "right"
      ? (this._splitSourceValue === "__translate__" ? this.splitTrBody : this.splitContainer)
      : this.container;
    target.focus({ preventScroll: true });
  }

  /** Set the split page offset to the current difference (rightPage - leftPage). */
  lockSplitOffset() {
    if (!this.splitOpen || !this.pdfViewer || !this.splitViewer) return;
    const leftPage = this.pdfViewer.currentPageNumber || 1;
    const rightPage = this.splitViewer.currentPageNumber || 1;
    const diff = rightPage - leftPage;
    this.splitOffset.value = String(diff);
    this.toast(`Page offset locked to ${diff >= 0 ? "+" : ""}${diff}.`);
  }

  /** Refresh the right-pane document selector with open tabs + Live translation. */
  _refreshSplitSources() {
    const tabs = this.tabs || [];
    const options = [
      el("option", { value: "__same__" }, `Same document (${this.activeTab?.name || "current"})`),
      el("option", { value: "__translate__" }, "Live translation (bilingual reader)"),
    ];
    for (const tab of tabs) {
      if (tab === this.activeTab) continue;
      options.push(el("option", { value: String(tab.id) }, tab.name));
    }
    this.splitSource.replaceChildren(...options);
    const valid = new Set(options.map((o) => o.value));
    if (!valid.has(this._splitSourceValue)) {
      const other = tabs.find((t) => t !== this.activeTab);
      this._splitSourceValue = other ? String(other.id) : "__same__";
    }
    this.splitSource.value = this._splitSourceValue;
    const isOtherTab = this._splitSourceValue !== "__same__" && this._splitSourceValue !== "__translate__";
    this.splitDiffBtn.hidden = !isOtherTab;
    if (!isOtherTab) this._clearSplitDiff();
  }

  /** Toggle side-by-side split view (Ctrl+\). */
  toggleSplitView() {
    if (this.splitOpen) return this.closeSplitView();
    return this.openSplitView();
  }

  /** Open side-by-side split view, optionally targeting a specific tab ID, "__same__", or "__translate__". */
  async openSplitView(target = null) {
    if (!this.doc) return;
    const tabs = this.tabs || [];
    if (target !== null) {
      this._splitSourceValue = String(target);
    } else if (!this.splitOpen) {
      const other = tabs.find((t) => t !== this.activeTab);
      this._splitSourceValue = other ? String(other.id) : "__same__";
    }
    this.splitOpen = true;
    this.splitBar.hidden = false;
    this.splitDivider.hidden = false;
    this.splitPane.hidden = false;
    this.root.classList.add("rd-split-open");
    this.splitContainer.classList.toggle("dark", this.container.classList.contains("dark"));
    this._applySplitSizes();
    this._setSplitActivePane("left");
    this._refreshSplitSources();
    this._wireSplitMainEvents();
    this._stepAside();
    await this._loadSplitSource();
  }

  _wireSplitMainEvents() {
    if (!this.eventBus || this._splitMainEventsWired) return;
    this._splitMainEventsWired = true;
    this.eventBus.on("pagechanging", () => {
      if (this.splitOpen && this._splitSourceValue === "__translate__") {
        this._refreshSplitTranslation();
      }
    });
    this.eventBus.on("pagerendered", ({ pageNumber }) => {
      if (this.splitOpen && this._splitDiffOn && this._splitDiff) {
        this._drawSplitDiffBoxes("left", pageNumber);
      }
    });
  }

  /** Close split view cleanly without affecting the left pane or its unsaved edits. */
  async closeSplitView() {
    if (!this.splitOpen) return;
    this._splitLoadId += 1;
    this._splitTrReqId += 1;
    this._clearSplitDiff();
    this.splitOpen = false;
    this.splitBar.hidden = true;
    this.splitDivider.hidden = true;
    this.splitPane.hidden = true;
    this.root.classList.remove("rd-split-open");
    this._applySplitSizes();
    this._setSplitActivePane("left");
    await this._closeSplitDoc();
    this._stepAside();
    if (this.doc) this.container.focus({ preventScroll: true });
  }

  async _closeSplitDoc() {
    const doc = this.splitDoc;
    const task = this.splitTask;
    this.splitDoc = null;
    this.splitTask = null;
    if (this.splitViewer) {
      try { this.splitViewer.setDocument(null); } catch (e) { /* already torn down */ }
    }
    if (this.splitLinkService) {
      try { this.splitLinkService.setDocument(null); } catch (e) { /* already torn down */ }
    }
    if (doc) await destroyTask(doc.loadingTask || task, false);
    else if (task) await destroyTask(task, false);
  }

  _ensureSplitViewer() {
    if (this.splitViewer) return;
    const { EventBus, PDFLinkService, PDFViewer } = viewer;
    this.splitEventBus = new EventBus();
    this.splitLinkService = new PDFLinkService({ eventBus: this.splitEventBus });
    this.splitViewer = new PDFViewer({
      container: this.splitContainer,
      viewer: this.splitViewerEl,
      eventBus: this.splitEventBus,
      linkService: this.splitLinkService,
      annotationMode: pdfjs.AnnotationMode.ENABLE,
      textLayerMode: 1,
      imageResourcesPath: BASE + "web/images/",
      removePageBorders: false,
    });
    this.splitLinkService.setViewer(this.splitViewer);
    this.splitEventBus.on("pagechanging", () => this._updateSplitPageStatus());
    this.splitEventBus.on("pagerendered", ({ pageNumber }) => {
      if (this.splitOpen && this._splitDiffOn && this._splitDiff) {
        this._drawSplitDiffBoxes("right", pageNumber);
      }
    });
    this.splitEventBus.on("pagesinit", () => {
      if (this.pdfViewer?.currentScaleValue) {
        this.splitViewer.currentScaleValue = this.pdfViewer.currentScaleValue;
      } else {
        this.splitViewer.currentScaleValue = "page-width";
      }
      this._updateSplitPageStatus();
      if (this.splitSync.checked) this._syncPane("left");
    });
    // Mirror left-pane zoom changes when sync is enabled
    if (this.eventBus && !this._splitZoomWired) {
      this._splitZoomWired = true;
      this.eventBus.on("scalechanging", ({ scale }) => {
        if (!this.splitOpen || !this.splitSync.checked || !this.splitViewer || !this.splitDoc) return;
        if (Math.abs((this.splitViewer.currentScale || 0) - scale) > 0.005) {
          this.splitViewer.currentScale = scale;
        }
      });
    }
  }

  _updateSplitPageStatus() {
    if (this._splitSourceValue === "__translate__") {
      this.splitPageStatus.textContent = `Page ${this.currentPage}`;
      this.splitPrevBtn.disabled = true;
      this.splitNextBtn.disabled = true;
      return;
    }
    if (!this.splitDoc || !this.splitViewer) {
      this.splitPageStatus.textContent = "";
      this.splitPrevBtn.disabled = true;
      this.splitNextBtn.disabled = true;
      return;
    }
    const n = this.splitViewer.currentPageNumber || 1;
    const total = this.splitViewer.pagesCount || this.splitDoc.numPages;
    this.splitPageStatus.textContent = `Page ${n} of ${total}`;
    this.splitPrevBtn.disabled = n <= 1;
    this.splitNextBtn.disabled = n >= total;
  }

  async _bytesForSplitTab(tab) {
    if (!tab) return null;
    if (tab === this.activeTab) return await this._fullBytes();
    if (tab.bytes) return tab.bytes;
    const url = tab.url || (tab.fileId ? `/api/file/${encodeURIComponent(tab.fileId)}` : null);
    if (url) {
      const res = await fetch(url, {
        headers: { "X-Pdfm-Token": this.token },
      });
      if (!res.ok) throw new Error(`Could not read ${tab.name}`);
      return new Uint8Array(await res.arrayBuffer());
    }
    return null;
  }

  async _initSplitTranslateLangs() {
    if (this.splitTrFrom.options.length > 0) return;
    const langs = await this._translateLanguages();
    const fromDef = store.get("pdfm-tr-from", "auto");
    const toDef = store.get("pdfm-tr-to", "es");
    this.splitTrFrom.replaceChildren(
      ...[["auto", "Auto-detect"], ...langs].map(([code, label]) => {
        const o = el("option", { value: code }, label);
        if (code === fromDef) o.selected = true;
        return o;
      }),
    );
    this.splitTrTo.replaceChildren(
      ...langs.map(([code, label]) => {
        const o = el("option", { value: code }, label);
        if (code === toDef) o.selected = true;
        return o;
      }),
    );
  }

  _renderSplitTranslationText() {
    const cur = this._splitTrCurrent;
    if (!cur) return;
    const txt = this._splitTrOrig
      ? (cur.original || "(No extractable text on this page.)")
      : (cur.translated || "(No extractable text on this page — run OCR first.)");
    this.splitTrBody.textContent = txt;
  }

  async _refreshSplitTranslation() {
    const tab = this.activeTab;
    if (!tab?.doc || this._splitSourceValue !== "__translate__") return;
    await this._initSplitTranslateLangs();
    const pageNum = this.currentPage || 1;
    const fromCode = this.splitTrFrom.value || "auto";
    const toCode = this.splitTrTo.value || "es";
    this.splitTitle.textContent = `Live translation · Page ${pageNum}`;
    this._updateSplitPageStatus();
    const key = `${this._key() || tab.name}:${pageNum}:${fromCode}:${toCode}`;
    if (this._splitTrCache.has(key)) {
      this._splitTrCurrent = this._splitTrCache.get(key);
      this.splitTrStatus.textContent = `${this._splitTrCurrent.src_lang} → ${this._splitTrCurrent.dst_lang}`;
      this._renderSplitTranslationText();
      return;
    }
    const reqId = ++this._splitTrReqId;
    this.splitTrStatus.textContent = "Translating…";
    this.splitTrBody.textContent = `Translating page ${pageNum}…`;
    try {
      const fd = new FormData();
      if (tab.fileId && this.isPristine()) {
        fd.append("id", tab.fileId);
      } else {
        const bytes = await this._currentBytes();
        fd.append("file", new Blob([bytes], { type: "application/pdf" }), tab.name || "document.pdf");
      }
      fd.append("page", String(pageNum));
      fd.append("from", fromCode);
      fd.append("to", toCode);
      if (tab.password) fd.append("password", tab.password);
      const res = await fetch("/api/translate/page", {
        method: "POST",
        headers: this.token ? { "X-Pdfm-Token": this.token } : {},
        body: fd,
      });
      const payload = await res.json();
      if (reqId !== this._splitTrReqId) return;
      if (!res.ok) throw new Error(payload.error || res.statusText);
      this._splitTrCache.set(key, payload);
      this._splitTrCurrent = payload;
      this.splitTrStatus.textContent = `${payload.src_lang} → ${payload.dst_lang}`;
      this._renderSplitTranslationText();
    } catch (e) {
      if (reqId !== this._splitTrReqId) return;
      this.splitTrStatus.textContent = "Error";
      this.splitTrBody.textContent = e?.message || String(e);
    }
  }

  /** Toggle interactive diff highlighting between left and right documents. */
  async toggleSplitDiff() {
    if (this._splitDiffOn) {
      this._clearSplitDiff();
      return;
    }
    const tabs = this.tabs || [];
    const rightTab = tabs.find((t) => String(t.id) === this._splitSourceValue);
    if (!this.activeTab || !rightTab || rightTab === this.activeTab) return;
    this.splitDiffBtn.disabled = true;
    this.splitDiffBtn.textContent = "Comparing…";
    try {
      const fd = new FormData();
      if (this.activeTab.fileId && this.isPristine()) {
        fd.append("old_id", this.activeTab.fileId);
      } else {
        const leftBytes = await this._fullBytes();
        fd.append("file_old", new Blob([leftBytes], { type: "application/pdf" }), this.activeTab.name || "old.pdf");
      }
      if (rightTab.fileId && !rightTab.bytes) {
        fd.append("new_id", rightTab.fileId);
      } else {
        const rightBytes = await this._bytesForSplitTab(rightTab);
        fd.append("file_new", new Blob([rightBytes], { type: "application/pdf" }), rightTab.name || "new.pdf");
      }
      if (this.activeTab.password) fd.append("old_password", this.activeTab.password);
      if (rightTab.password) fd.append("new_password", rightTab.password);
      fd.append("mode", "both");
      const res = await fetch("/api/reader/compare", {
        method: "POST",
        headers: this.token ? { "X-Pdfm-Token": this.token } : {},
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      this._splitDiff = data;
      this._splitDiffOn = true;
      this.splitDiffBtn.classList.add("on");
      this.splitDiffBtn.textContent = "Hide diff";
      const changed = this._splitDiffChangedRows();
      this.splitDiffPrev.hidden = changed.length === 0;
      this.splitDiffNext.hidden = changed.length === 0;
      this.splitDiffStatus.hidden = false;
      this._splitDiffIdx = changed.length ? 0 : -1;
      this.splitDiffStatus.textContent = changed.length
        ? `1 / ${changed.length} changed`
        : "No differences found";
      this._redrawAllSplitDiffBoxes();
      if (changed.length) this._jumpSplitDiff(0);
    } catch (e) {
      this.toast("Compare failed: " + (e?.message || e));
      this._clearSplitDiff();
    } finally {
      this.splitDiffBtn.disabled = false;
    }
  }

  _splitDiffChangedRows() {
    return (this._splitDiff?.changes || []).filter((r) => r.kind !== "same");
  }

  _clearSplitDiff() {
    this._splitDiff = null;
    this._splitDiffOn = false;
    this._splitDiffIdx = -1;
    if (this.splitDiffBtn) {
      this.splitDiffBtn.classList.remove("on");
      this.splitDiffBtn.textContent = "Highlight diff";
    }
    if (this.splitDiffPrev) this.splitDiffPrev.hidden = true;
    if (this.splitDiffNext) this.splitDiffNext.hidden = true;
    if (this.splitDiffStatus) {
      this.splitDiffStatus.hidden = true;
      this.splitDiffStatus.textContent = "";
    }
    this.root?.querySelectorAll(".rd-diff-layer").forEach((n) => n.remove());
  }

  _jumpSplitDiff(delta) {
    const changed = this._splitDiffChangedRows();
    if (!changed.length) return;
    if (delta !== 0) {
      this._splitDiffIdx = (this._splitDiffIdx + delta + changed.length) % changed.length;
    }
    const row = changed[Math.max(0, this._splitDiffIdx)];
    this.splitDiffStatus.textContent = `${this._splitDiffIdx + 1} / ${changed.length} · ${row.label || row.kind}`;
    this._splitSyncing = true;
    try {
      if (row.old && this.pdfViewer) this.pdfViewer.currentPageNumber = row.old;
      if (row.new && this.splitViewer) this.splitViewer.currentPageNumber = row.new;
    } finally {
      requestAnimationFrame(() => { this._splitSyncing = false; });
    }
  }

  _redrawAllSplitDiffBoxes() {
    if (!this._splitDiffOn || !this._splitDiff) return;
    if (this.pdfViewer) {
      for (let i = 1; i <= (this.pdfViewer.pagesCount || 0); i++) this._drawSplitDiffBoxes("left", i);
    }
    if (this.splitViewer) {
      for (let i = 1; i <= (this.splitViewer.pagesCount || 0); i++) this._drawSplitDiffBoxes("right", i);
    }
  }

  _drawSplitDiffBoxes(side, pageNumber) {
    const v = side === "left" ? this.pdfViewer : this.splitViewer;
    const view = v?.getPageView(pageNumber - 1);
    if (!view?.div || !view.viewport) return;
    view.div.querySelector(".rd-diff-layer")?.remove();
    const row = (this._splitDiff?.changes || []).find((r) => (side === "left" ? r.old === pageNumber : r.new === pageNumber));
    if (!row || row.kind === "same") return;
    const dpi = Number(this._splitDiff?.dpi) || 90;
    const pxToPt = 72 / dpi;
    const scale = view.viewport.scale || 1;
    const layer = el("div", { class: `rd-diff-layer rd-diff-${side}` });
    const areas = row.areas || [];
    if (areas.length) {
      for (const [x1, y1, x2, y2] of areas) {
        layer.append(el("div", {
          class: `rd-diff-box rd-diff-box-${side}`,
          style: `left:${x1 * pxToPt * scale}px;top:${y1 * pxToPt * scale}px;`
            + `width:${Math.max(6, (x2 - x1) * pxToPt * scale)}px;`
            + `height:${Math.max(6, (y2 - y1) * pxToPt * scale)}px;`,
        }));
      }
    } else {
      // Whole-page added or removed indicator
      layer.append(el("div", { class: `rd-diff-banner rd-diff-banner-${side}` }, row.label || row.kind));
    }
    view.div.append(layer);
  }

  async _loadSplitSource() {
    const loadId = ++this._splitLoadId;
    const isTranslate = this._splitSourceValue === "__translate__";
    this.splitContainer.hidden = isTranslate;
    this.splitTranslatePane.hidden = !isTranslate;
    const isOtherTab = !isTranslate && this._splitSourceValue !== "__same__";
    this.splitDiffBtn.hidden = !isOtherTab;
    if (isTranslate) {
      await this._closeSplitDoc();
      await this._refreshSplitTranslation();
      return;
    }

    this.splitLoading.hidden = false;
    try {
      await loadPdfjs();
      if (!this.splitOpen || loadId !== this._splitLoadId) return;
      this._ensureSplitViewer();
      await this._closeSplitDoc();
      if (!this.splitOpen || loadId !== this._splitLoadId) return;

      const tabs = this.tabs || [];
      const tab = this._splitSourceValue === "__same__"
        ? this.activeTab
        : (tabs.find((t) => String(t.id) === this._splitSourceValue) || this.activeTab);
      if (!tab) return;

      this.splitTitle.textContent = this._splitSourceValue === "__same__"
        ? `${tab.name} (2nd view)`
        : tab.name;

      const raw = await this._bytesForSplitTab(tab);
      if (!raw || !this.splitOpen || loadId !== this._splitLoadId) return;

      const task = openDocument(pdfjs, {
        ...DOC_OPTIONS,
        data: new Uint8Array(raw),
        password: tab.password || undefined,
      });
      this.splitTask = task;
      const doc = await task.promise;
      if (!this.splitOpen || loadId !== this._splitLoadId) {
        await destroyTask(task, false);
        return;
      }
      this.splitDoc = doc;
      this.splitLinkService.setDocument(doc);
      this.splitViewer.setDocument(doc);
      this._updateSplitPageStatus();
    } catch (e) {
      if (this.splitOpen && loadId === this._splitLoadId) {
        this.toast("Could not open second view: " + (e?.message || e));
      }
    } finally {
      if (loadId === this._splitLoadId) this.splitLoading.hidden = true;
    }
  }

  /** Synchronize scroll position from ``from`` ("left" or "right") to the opposite pane. */
  _syncPane(from) {
    if (!this.splitOpen || !this.doc || !this.splitDoc || !this.pdfViewer || !this.splitViewer) return;
    if (this._splitSourceValue === "__translate__") return;
    const srcC = from === "left" ? this.container : this.splitContainer;
    const dstC = from === "left" ? this.splitContainer : this.container;
    const srcV = from === "left" ? this.pdfViewer : this.splitViewer;
    const dstV = from === "left" ? this.splitViewer : this.pdfViewer;
    const offset = Math.round(Number(this.splitOffset.value) || 0);
    const signedOffset = from === "left" ? offset : -offset;

    const srcMax = Math.max(1, srcC.scrollHeight - srcC.clientHeight);
    const dstMax = Math.max(0, dstC.scrollHeight - dstC.clientHeight);
    if (dstMax <= 0) return;

    this._splitSyncing = true;
    try {
      const srcPage = srcV.currentPageNumber || 1;
      const dstCount = dstV.pagesCount || (from === "left" ? this.splitDoc.numPages : this.doc.numPages);
      let targetPage = Math.max(1, Math.min(dstCount, srcPage + signedOffset));
      // When interactive diff alignment is active, use matched page pairs (row.old <-> row.new)
      if (this._splitDiffOn && this._splitDiff?.changes) {
        const matched = this._splitDiff.changes.find((r) => (from === "left" ? r.old === srcPage : r.new === srcPage));
        const aligned = from === "left" ? matched?.new : matched?.old;
        if (aligned) targetPage = Math.max(1, Math.min(dstCount, aligned));
      }
      const srcView = srcV.getPageView(srcPage - 1);
      const dstView = dstV.getPageView(targetPage - 1);
      if (srcView?.div && dstView?.div && srcView.div.offsetHeight > 0) {
        const within = (srcC.scrollTop - srcView.div.offsetTop) / srcView.div.offsetHeight;
        const clampedWithin = Math.max(-0.2, Math.min(1.2, within));
        const targetTop = dstView.div.offsetTop + clampedWithin * dstView.div.offsetHeight;
        dstC.scrollTop = Math.max(0, Math.min(dstMax, targetTop));
      } else {
        dstC.scrollTop = (srcC.scrollTop / srcMax) * dstMax;
      }
    } finally {
      requestAnimationFrame(() => { this._splitSyncing = false; });
    }
  }
};
