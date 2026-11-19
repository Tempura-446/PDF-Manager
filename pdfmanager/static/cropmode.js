/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Interactive visual crop mode for the reader: drag a shaded crop rectangle
// directly over the current page with corner/edge handles, live width/height
// readout in pt / mm / in, page scope selector (this/odd/even/range/all),
// optional permanent trim, and full Undo/Redo + Save via _changeDocument().
// A part of Reader (see CropMode below).

import { el, svg } from "./readerkit.js";

const PT_PER_MM = 72 / 25.4;

function formatDim(pt, unit) {
  if (unit === "mm") return `${(pt / PT_PER_MM).toFixed(1)} mm`;
  if (unit === "in") return `${(pt / 72).toFixed(2)} in`;
  return `${Math.round(pt)} pt`;
}

/** Reader's interactive visual crop mode, as a class it extends. */
export const CropMode = (Base) => class extends Base {
  /** Build the crop toolbar (from #build). */
  _buildCropMode() {
    this.cropMode = false;
    this._cropOverlay = null;
    this._cropPage = 1;
    this._cropRect = null; // [x0, y0, x1, y1] in visual PDF points (bottom-left origin)
    this._cropPageSize = [612, 792];

    this.cropScope = el("select", { class: "rd-zoom", "aria-label": "Crop scope" },
      el("option", { value: "page" }, "This page"),
      el("option", { value: "odd" }, "Odd pages"),
      el("option", { value: "even" }, "Even pages"),
      el("option", { value: "range" }, "Page range"),
      el("option", { value: "all" }, "All pages"));
    this.cropRange = el("input", {
      type: "text", class: "rd-crop-range", placeholder: "e.g. 1-3, 5",
      "aria-label": "Pages to crop", hidden: true,
    });
    this.cropUnit = el("select", { class: "rd-zoom", "aria-label": "Units" },
      el("option", { value: "in" }, "in"),
      el("option", { value: "mm" }, "mm"),
      el("option", { value: "pt" }, "pt"));
    this.cropPermanent = el("input", { type: "checkbox" });
    this.cropReadout = el("span", { class: "rd-crop-readout", "aria-live": "polite" });
    this.cropApplyBtn = el("button", { class: "rd-btn primary", type: "button" }, "Apply crop");
    const cropClose = el("button", {
      class: "rd-btn", type: "button", title: "Cancel crop (Esc)", "aria-label": "Cancel crop", html: svg("x"),
    });

    this.cropbar = el("div", {
      class: "rd-findbar rd-cropbar", hidden: true, role: "toolbar", "aria-label": "Crop pages",
    },
      el("span", { class: "rd-overlay-title" }, "Crop"),
      el("label", {}, "Apply to", this.cropScope),
      this.cropRange,
      el("label", {}, "Units", this.cropUnit),
      el("label", { title: "Clip content outside the box permanently instead of only updating /CropBox" },
        this.cropPermanent, "Trim permanently"),
      el("span", { class: "rd-sep" }),
      this.cropReadout,
      el("span", { style: "flex:1" }),
      this.cropApplyBtn,
      cropClose);

    this.cropScope.addEventListener("change", () => {
      this.cropRange.hidden = this.cropScope.value !== "range";
      if (!this.cropRange.hidden) this.cropRange.focus();
    });
    this.cropUnit.addEventListener("change", () => this._updateCropReadout());
    this.cropApplyBtn.addEventListener("click", () => this.applyCrop());
    cropClose.addEventListener("click", () => this.closeCropMode());
  }

  /** Open interactive visual crop mode on the current page. */
  openCropMode() {
    if (!this.doc || !this.pdfViewer) return;
    if (this.cropMode) return;
    this._finishEditing();
    this.cropMode = true;
    this.cropbar.hidden = false;
    this._cropPage = this.pdfViewer.currentPageNumber || 1;
    const view = this.pdfViewer.getPageView(this._cropPage - 1);
    const vp = view?.viewport;
    if (!view?.div || !vp) {
      this.closeCropMode();
      this.toast("Page is still loading; try again in a moment.");
      return;
    }
    const [wPt, hPt] = [vp.viewBox[2] - vp.viewBox[0], vp.viewBox[3] - vp.viewBox[1]];
    const rotated = (vp.rotation % 180) !== 0;
    const vw = rotated ? hPt : wPt;
    const vh = rotated ? wPt : hPt;
    this._cropPageSize = [vw, vh];
    // Start with a 10% inset crop box so handles are immediately visible and draggable.
    this._cropRect = [vw * 0.1, vh * 0.1, vw * 0.9, vh * 0.9];
    this._mountCropOverlay(view.div, vp);
    this._updateCropReadout();
    this._stepAside();
  }

  /** Close crop mode without modifying the document. */
  closeCropMode() {
    if (!this.cropMode) return;
    this.cropMode = false;
    this.cropbar.hidden = true;
    this._cropOverlay?.remove();
    this._cropOverlay = null;
    this._stepAside();
  }

  _updateCropReadout() {
    if (!this._cropRect) {
      this.cropReadout.textContent = "";
      return;
    }
    const [x0, y0, x1, y1] = this._cropRect;
    const unit = this.cropUnit.value || "in";
    this.cropReadout.textContent = `${formatDim(x1 - x0, unit)} × ${formatDim(y1 - y0, unit)}`;
  }

  _mountCropOverlay(pageDiv, vp) {
    this._cropOverlay?.remove();
    const overlay = el("div", { class: "rd-crop-overlay" });
    const box = el("div", { class: "rd-crop-box" });
    const handles = ["nw", "n", "ne", "e", "se", "s", "sw", "w"].map((dir) =>
      el("div", { class: `rd-crop-handle rd-crop-h-${dir}`, "data-dir": dir })
    );
    box.append(...handles);
    overlay.append(box);
    pageDiv.append(overlay);
    this._cropOverlay = overlay;

    const [vw, vh] = this._cropPageSize;
    const renderBox = () => {
      const [x0, y0, x1, y1] = this._cropRect;
      const leftPct = (x0 / vw) * 100;
      const topPct = ((vh - y1) / vh) * 100;
      const widthPct = ((x1 - x0) / vw) * 100;
      const heightPct = ((y1 - y0) / vh) * 100;
      box.style.left = `${leftPct}%`;
      box.style.top = `${topPct}%`;
      box.style.width = `${widthPct}%`;
      box.style.height = `${heightPct}%`;
      this._updateCropReadout();
    };
    renderBox();

    let mode = null;
    let startPt = null;
    let startRect = null;

    const clientToPdf = (clientX, clientY) => {
      const r = overlay.getBoundingClientRect();
      const nx = Math.max(0, Math.min(1, (clientX - r.left) / Math.max(1, r.width)));
      const ny = Math.max(0, Math.min(1, (clientY - r.top) / Math.max(1, r.height)));
      return [nx * vw, (1 - ny) * vh];
    };

    overlay.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const handle = e.target.closest?.(".rd-crop-handle");
      startPt = clientToPdf(e.clientX, e.clientY);
      startRect = [...this._cropRect];
      if (handle) {
        mode = handle.dataset.dir;
      } else if (e.target === box) {
        mode = "move";
      } else {
        mode = "new";
        this._cropRect = [startPt[0], startPt[1], startPt[0] + 4, startPt[1] + 4];
      }
      overlay.setPointerCapture?.(e.pointerId);
    });

    overlay.addEventListener("pointermove", (e) => {
      if (!mode) return;
      const [px, py] = clientToPdf(e.clientX, e.clientY);
      let [x0, y0, x1, y1] = startRect;
      const minGap = 12;
      if (mode === "new") {
        x0 = Math.min(startPt[0], px);
        x1 = Math.max(startPt[0], px);
        y0 = Math.min(startPt[1], py);
        y1 = Math.max(startPt[1], py);
      } else if (mode === "move") {
        const dx = px - startPt[0];
        const dy = py - startPt[1];
        const w = x1 - x0, h = y1 - y0;
        x0 = Math.max(0, Math.min(vw - w, x0 + dx));
        y0 = Math.max(0, Math.min(vh - h, y0 + dy));
        x1 = x0 + w;
        y1 = y0 + h;
      } else {
        if (mode.includes("w")) x0 = Math.max(0, Math.min(x1 - minGap, px));
        if (mode.includes("e")) x1 = Math.min(vw, Math.max(x0 + minGap, px));
        if (mode.includes("s")) y0 = Math.max(0, Math.min(y1 - minGap, py));
        if (mode.includes("n")) y1 = Math.min(vh, Math.max(y0 + minGap, py));
      }
      if (x1 - x0 >= 4 && y1 - y0 >= 4) {
        this._cropRect = [x0, y0, x1, y1];
        renderBox();
      }
    });

    const endDrag = (e) => {
      if (!mode) return;
      mode = null;
      if (e?.pointerId != null) overlay.releasePointerCapture?.(e.pointerId);
    };
    overlay.addEventListener("pointerup", endDrag);
    overlay.addEventListener("pointercancel", endDrag);
  }

  /** Apply the current crop rectangle through /api/crop with full Undo/Redo support. */
  async applyCrop() {
    if (!this.cropMode || !this._cropRect) return;
    const rect = this._cropRect.map((v) => Math.round(v * 100) / 100);
    const scope = this.cropScope.value || "page";
    const pages = this.cropRange.value.trim();
    const page = this._cropPage;
    const trimPermanently = this.cropPermanent.checked;
    this.closeCropMode();
    await this._changeDocument(
      (bytes, tab) => this._post("/api/crop", bytes, tab, {
        rect: JSON.stringify(rect),
        scope,
        page: String(page),
        pages,
        trim_permanently: trimPermanently ? "true" : "false",
      }),
      { failure: "Could not crop pages: " },
    );
    this.toast(trimPermanently ? "Pages cropped permanently." : "Pages cropped.");
  }
};
