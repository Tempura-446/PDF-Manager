/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Side-by-side split view for the reader: shows either a second open tab or a
// second independent view of the current document beside the main pane, with a
// draggable divider, optional synchronized scroll & zoom, page-offset lock,
// and keyboard pane switching (Ctrl+\, F6 / Ctrl+Shift+\).
// A part of Reader (see SplitView below).

import { BASE, DOC_OPTIONS, destroyTask, openDocument } from "./pdfjs.js";
import { el, loadPdfjs, pdfjs, svg, viewer } from "./readerkit.js";

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
    this.splitLoading = el("div", { class: "rd-loading", hidden: true },
      el("div", { class: "rd-spinner", role: "progressbar", "aria-label": "Loading second view" }));
    this.splitPane = el("section", {
      class: "rd-split-pane", hidden: true, role: "region", "aria-label": "Second view",
    }, splitHead, el("div", { class: "rd-split-stage" }, this.splitContainer, this.splitLoading));

    // Wire split bar controls
    this.splitSource.addEventListener("change", () => {
      this._splitSourceValue = this.splitSource.value;
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
    this.splitSwitchBtn.addEventListener("click", () => this.switchSplitPane());
    splitClose.addEventListener("click", () => this.closeSplitView());
    this.splitPrevBtn.addEventListener("click", () => this.splitViewer?.previousPage());
    this.splitNextBtn.addEventListener("click", () => this.splitViewer?.nextPage());

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
    (next === "right" ? this.splitContainer : this.container).focus({ preventScroll: true });
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

  /** Refresh the right-pane document selector with open tabs. */
  _refreshSplitSources() {
    const tabs = this.tabs || [];
    const options = [el("option", { value: "__same__" }, `Same document (${this.activeTab?.name || "current"})`)];
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
  }

  /** Toggle side-by-side split view (Ctrl+\). */
  toggleSplitView() {
    if (this.splitOpen) return this.closeSplitView();
    return this.openSplitView();
  }

  /** Open side-by-side split view, optionally targeting a specific tab ID or "__same__". */
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
    this._stepAside();
    await this._loadSplitSource();
  }

  /** Close split view cleanly without affecting the left pane or its unsaved edits. */
  async closeSplitView() {
    if (!this.splitOpen) return;
    this._splitLoadId += 1;
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
    const url = tab.url || (tab.fileId ? `/api/open/${encodeURIComponent(tab.fileId)}` : null);
    if (url) {
      const res = await fetch(url, {
        headers: { "X-Pdfm-Token": this.token },
      });
      if (!res.ok) throw new Error(`Could not read ${tab.name}`);
      return new Uint8Array(await res.arrayBuffer());
    }
    return null;
  }

  async _loadSplitSource() {
    const loadId = ++this._splitLoadId;
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
      const targetPage = Math.max(1, Math.min(dstCount, srcPage + signedOffset));
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
