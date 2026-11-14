// The reader's drawing scale and measuring: for to-scale drawings such as
// house plans, each sheet's scale (read from the sheet, or set by hand), zoom
// to true paper size or to a drawing scale on a calibrated screen, a tool to
// measure lengths, paths, areas, counts and angles, their list, and keeping
// them with the file. A part of Reader (see Measuring below); the arithmetic
// is measure.js's.

import { el, isTyping, MAX_ZOOM, MIN_ZOOM, pdfjs, store, svg, viewer } from "./readerkit.js";
import * as measure from "./measure.js";
let snap = null;  // snapping to a drawing's lines (snap.js); the reader works without it
const snapReady = import("./snap.js").then((m) => { snap = m; }, () => {});
let measureSeq = 0;  // numbers the measurements
// Measurement groups' colours, in the order the groups are made.
const GROUP_COLORS = ["#2b6cb0", "#c0392b", "#1f8a3b", "#c77700", "#7b3fb5", "#0f8a8a", "#b83280", "#5a6b2c"];
const MEASURE_KINDS = { distance: "Length", path: "Path", area: "Area", count: "Count", angle: "Angle" };
const SNAP_PX = 12;  // how close (on screen) a measuring click snaps to a line of the drawing
const GEOMETRY_PAGES = 8;  // how many pages' lines to keep per document
const isScale = (s) => !!s && typeof s === "object" && Number.isFinite(s.ratio) && s.ratio > 0 && (s.units === "ft" || s.units === "m") && typeof s.label === "string";

/** Reader's measuring, as a class it extends (``class Reader extends
 *  Measuring(Object)``): the same object, its methods in a file of their own.
 *  What the rest of the reader calls here, and what this calls there, has a
 *  name starting with _ (a private #name can't be shared between files). */
export const Measuring = (Base) => class extends Base {
  // ---- drawing scale and measuring ---------------------------------------------
  // For to-scale drawings such as house plans: each sheet's scale (read from
  // the sheet, or set by hand), zoom to true paper size or to a drawing
  // scale on a calibrated screen, and a tool to measure lengths and areas.

  _buildScaleBar() {
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
    this.viewAtSelect.addEventListener("change", () => { if (this.viewAtSelect.value) this._viewAt(this.viewAtSelect.value); });
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
    this.listBtn.addEventListener("click", () => this._toggleMeasPanel());
    this.calibrateBtn.addEventListener("click", () => this.calibrateScreen());
    close.addEventListener("click", () => this.toggleScaleBar(false));
  }

  toggleScaleBar(open = this.scalebar.hidden) {
    if (open && !this.doc) return;
    this.scalebar.hidden = !open;
    this.scaleBtn.setAttribute("aria-expanded", String(open));
    if (open) this._refreshScale(true);
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

  #scaleKey() { return "pdfm-scale:" + this._key(); }

  /** The current page's scale: what is known is shown at once. Reading it
   *  from the sheet (once per page) waits until the page is drawn and has
   *  been stayed on for a moment: the text is read by the same pdf.js worker
   *  that draws the pages, and a page that is only passed isn't read at all.
   *  ``now``: asked for (the scale bar was opened), so no waiting to see
   *  whether the page is stayed on. */
  _refreshScale(now = false) {
    const tab = this.activeTab, doc = this.doc;
    if (!tab || !doc) return;
    if (!tab.scales) {
      const saved = store.get(this.#scaleKey(), {});
      tab.scales = {};
      if (saved && typeof saved === "object") for (const [k, v] of Object.entries(saved)) if (v === false || isScale(v)) tab.scales[k] = v;
    }
    tab.detected ||= new Map();
    this.#showScale();
    clearTimeout(this.scaleTimer);
    const n = this.pdfViewer.currentPageNumber || 1;
    if (tab.detected.has(n)) return;
    // While measuring, a scale stored in the PDF is looked up at once (it's
    // already here: one answer from the server for every page); reading the
    // sheet's text still waits for the page to be drawn.
    this.scaleTimer = setTimeout(() => this.#readScale(tab, doc, n, now), now || this.measureMode ? 0 : 400);
  }

  async #readScale(tab, doc, n, now = false) {
    const here = () => doc === this.doc && tab === this.activeTab && (this.pdfViewer.currentPageNumber || 1) === n;
    if (!here() || tab.detected.has(n) || tab.readingScale === n) return;
    tab.readingScale = n;
    try {
      // A scale the drawing program stored in the PDF is exact; the one
      // written on the sheet is read from its text.
      tab.stored ||= this.#storedScales(tab);
      const stored = (await tab.stored)[n]?.[0];
      if (!here()) return;
      if (!stored && !now) {
        await this.#drawn(n);
        if (!here()) return;  // left meanwhile: read when it's come back to
      }
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
      } catch (e) { tab.detected.set(n, null); /* no text: no scale */ }
    } finally {
      if (tab.readingScale === n) tab.readingScale = 0;
    }
    if (doc === this.doc && tab === this.activeTab) this.#showScale();
  }

  /** Resolves when page ``n`` is drawn (at once if it is), or left for another page. */
  #drawn(n) {
    const view = this.pdfViewer.getPageView(n - 1);
    if (!view || view.renderingState === 3) return Promise.resolve();
    return new Promise((resolve) => {
      const bus = this.eventBus;
      const done = () => { bus.off("pagerendered", rendered); bus.off("pagechanging", done); clearTimeout(timer); resolve(); };
      const rendered = (e) => { if (e.pageNumber === n) done(); };
      const timer = setTimeout(done, 15000);  // (a page that can't be drawn)
      bus.on("pagerendered", rendered);
      bus.on("pagechanging", done);
    });
  }

  /** {page: [viewport scale, biggest first]} stored in the PDF (see viewports.py). */
  async #storedScales(tab) {
    try {
      const body = new FormData();
      if (tab.fileId && !tab.edited) body.append("id", tab.fileId);
      else {
        const bytes = tab === this.activeTab ? await this._fullBytes() : tab.bytes;  // (annotations don't matter here)
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
    this._syncViewAt();
    this._drawAllMeasures();
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
  _trueZoom() {
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
  async _viewAt(value) {
    if (!this.doc) return;
    if (!this.#screenPpi() && !(await this.calibrateScreen(true))) { this.#syncZoomSelect(); this._syncViewAt(); return; }
    let zoom = this._trueZoom(), what = "true paper size";
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
    this._syncViewAt();
    this.toast(`Showing the drawing at ${what} on your screen`, 2500);
  }

  /** Zoom to scale shows what the zoom is now: true paper size, as if
   *  printed at a scale, or not to scale (and the zoom). */
  _syncViewAt() {
    const sel = this.viewAtSelect;
    if (!sel?.options.length || !this.pdfViewer || !this.doc) return;
    const zoom = this.pdfViewer.currentScale;
    let value = "";
    if (this.#screenPpi() && Number.isFinite(zoom)) {
      const t = this._trueZoom(), same = (z) => Math.abs(zoom / z - 1) < 0.002;
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
    const answer = await this._dialog("Calibrate your screen", body, [["cancel", "Cancel"], ["ok", "Save", true]]);
    if (answer !== "ok" || !Number.isFinite(cssPpi)) return false;
    this.screenPpi = cssPpi * dpr;
    store.set("pdfm-screen-ppi", this.screenPpi);
    this.toast("Screen calibrated: true paper size now matches the real world.", 3000);
    if (this.zoomPreset === "true-size" && this.pdfViewer) this.pdfViewer.currentScale = this._trueZoom();
    return true;
  }

  /** Start measuring ("distance" or "area"), set the scale from a line of
   *  known length ("calibrate"), or stop (null). */
  measureTool(mode) {
    if (mode && !this.doc) return;
    if (!mode && !this.measureMode) return;
    if (mode) {
      if (this.textEditMode || (this.editMode && this.editMode !== "NONE")) this._setEditMode("NONE");
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
    if (page) this._drawMeasures(page);
    if (aimPage && aimPage !== page) this._drawMeasures(aimPage);
    if (mode) { this._geometry(this.pdfViewer.currentPageNumber || 1); this._refreshScale(true); }  // start reading the lines and the scale now
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
    this._drawAllMeasures();
  }

  /** The lines of page ``n`` (see snap.js), read from the file the first
   *  time they're needed; null while they're being read. */
  _geometry(n) {
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
      if (this.draft?.page === n || this.aim?.page === n) this._drawMeasures(n);
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
        const geometry = this._geometry(n);
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

  _measureDown(e) {
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
      this._drawMeasures(at.page);
      return;
    }
    const d = this.draft;
    if (d.kind === "angle" && d.pts.length < 2) { d.pts.push(at.pdf); this._drawMeasures(at.page); return; }
    if (d.kind === "area" || d.kind === "path" || d.kind === "cutout") {
      const screen = (p) => at.view.viewport.convertToViewportPoint(...p);
      const [fx, fy] = screen(d.pts[0]), [lx, ly] = screen(d.pts.at(-1));
      const closed = d.kind !== "path";
      const nearFirst = closed && d.pts.length >= 3 && Math.hypot(at.x - fx, at.y - fy) < 9;
      const onLast = Math.hypot(at.x - lx, at.y - ly) < 4;  // the second click of a double-click
      if (nearFirst || (onLast && d.pts.length >= (closed ? 3 : 2))) { this.#finishMeasure(); return; }
      if (!onLast) d.pts.push(at.pdf);
      this._drawMeasures(at.page);
      return;
    }
    d.pts.push(at.pdf);
    this.#finishMeasure();
  }

  _measureMove(e) {
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
      if (n) this._drawMeasures(n);
      if (before && before !== n) this._drawMeasures(before);
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
    this.moveFrame = requestAnimationFrame(() => { this.moveFrame = null; this._drawMeasures(g.page); this.#renderMeasPanel(); });
  }

  _measureUp() {
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
    this._drawMeasures(page);
    this.#renderMeasPanel();
  }

  async #finishMeasure() {
    const d = this.draft;
    this.draft = null;
    if (!d) return;
    if (d.kind === "calibrate") { this._drawMeasures(d.page); await this.#scaleFromLine(d); return; }
    const least = { area: 3, cutout: 3, angle: 3 }[d.kind] || 2;
    if (d.pts.length < least) { this._drawMeasures(d.page); return; }
    const tab = this.activeTab;
    tab.measures ||= [];
    if (d.kind === "cutout") { this.#addCutout(d); return; }
    const rect = d.kind === "rect";
    if (rect) {  // two corners on one line are no rectangle
      const view = this.pdfViewer.getPageView(d.page - 1)?.viewport;
      const [ax, ay] = view.convertToViewportPoint(...d.pts[0]), [bx, by] = view.convertToViewportPoint(...d.pts[1]);
      if (Math.abs(bx - ax) < 3 || Math.abs(by - ay) < 3) { this._drawMeasures(d.page); this.toast("Click two opposite corners of the rectangle.", 3000); return; }
    }
    this.#rememberMeasures();
    const m = { id: ++measureSeq, page: d.page, kind: rect ? "area" : d.kind, pts: rect ? measure.rectangle(d.pts[0], d.pts[1]) : d.pts };
    if (tab.measureGroup) m.group = tab.measureGroup;
    tab.measures.push(m);
    this.selected = { id: m.id, index: m.pts.length - 1 };  // the arrow keys nudge the last point
    this._drawMeasures(d.page);
    // The list opens with the first measurement, unless it was closed before.
    if (this.measPanel.hidden && !store.get("pdfm-measure-list-closed", false)) this._toggleMeasPanel(true, false);
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
    this._drawMeasures(at.page);
    if (this.measPanel.hidden && !store.get("pdfm-measure-list-closed", false)) this._toggleMeasPanel(true, false);
    else this.#renderMeasPanel();
  }

  /** A cut-out (``d``, just drawn) comes off the area it's drawn in. */
  #addCutout(d) {
    const tab = this.activeTab, middle = measure.centre(d.pts);
    const area = [...tab.measures].reverse().find((m) => m.page === d.page && m.kind === "area" && measure.inside(middle, m.pts));
    if (!area) {
      this._drawMeasures(d.page);
      this.toast("Draw the cut-out inside an area you've measured: it's taken off that area.", 5000);
      return;
    }
    this.#rememberMeasures();
    (area.holes ||= []).push(d.pts);
    this.selected = { id: area.id, index: null };
    this._drawMeasures(d.page);
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
      const pending = this._dialog("How long is that line?", [now, el("p", {}, "Type its real length, as the drawing gives it:"), input, error, result, scope],
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
  _measureKey(e) {
    if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.key === "Tab" && this.aim?.cands?.length > 1 && this.lastMove) {
      // Lines close together: the next point there is to snap to.
      const step = e.shiftKey ? -1 : 1;
      this.pick = { page: this.aim.page, x: this.lastMove.clientX, y: this.lastMove.clientY, index: (this.aim.index || 0) + step };
      this._measureMove(this.lastMove);
      return true;
    }
    if (e.key === "Escape") {
      if (this.draft) { const n = this.draft.page; this.draft = null; this._drawMeasures(n); }
      else if (this.selected) { const m = this.#selectedMeasure(); this.selected = null; if (m) this._drawMeasures(m.page); }
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
      if (this.draft && this.draft.pts.length > 1) { this.draft.pts.pop(); this._drawMeasures(this.draft.page); }
      else if (this.draft) { const n = this.draft.page; this.draft = null; this._drawMeasures(n); }
      else if (this.#selectedMeasure()?.kind === "count" && this.#selectedMeasure().pts.length > 1) {  // one mark of a count
        const m = this.#selectedMeasure();
        this.#rememberMeasures();
        m.pts.splice(Number.isInteger(this.selected.index) ? this.selected.index : m.pts.length - 1, 1);
        this.selected = { id: m.id, index: m.pts.length - 1 };
        this._drawMeasures(m.page);
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
    this._drawMeasures(m.page);
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
  _measureUndo(redo) {
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
    for (const n of pages) this._drawMeasures(n);
    this.#renderMeasPanel();
    this.toast(redo ? "Measurement change redone" : "Measurement change undone", 1500);
    return true;
  }

  _drawAllMeasures() {
    if (!this.pdfViewer || !this.doc) return;
    const pages = new Set([...(this.activeTab?.measures || []).map((m) => m.page), ...(this.draft ? [this.draft.page] : [])]);
    for (const layer of this.viewerEl.querySelectorAll("svg.rd-measures")) pages.add(Number(layer.closest(".page")?.dataset.pageNumber));
    for (const n of pages) if (n) this._drawMeasures(n);
    this.#renderMeasPanel();
  }

  /** Draw page ``n``'s measurements (and the one being made) over it. */
  _drawMeasures(n) {
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
  _toggleMeasPanel(open = this.measPanel.hidden, byHand = true) {
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
    units.addEventListener("change", () => { this.units = units.value; store.set("pdfm-measure-units", this.units); this._drawAllMeasures(); });
    const close = el("button", { class: "rd-btn", type: "button", title: "Close the list", "aria-label": "Close the list", html: svg("x") });
    close.addEventListener("click", () => this._toggleMeasPanel(false));
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
      button("csv", "CSV", "Save the measurements as a CSV file, for Excel or an estimate", () => this._exportMeasures()),
      button("pdfin", "Into PDF", "Write the measurements into the PDF as measurement annotations, so they show in any viewer and print", () => this._measuresIntoPdf()),
      el("span", { style: "flex:1" }),
      button("trash", "", "Remove every measurement", () => this.clearMeasures()));
    this.measPanel.replaceChildren(...[head, unitRow, groupRow, list, totals.length ? el("div", { class: "rd-mtotals" }, ...totals) : null, foot].filter(Boolean));
  }

  /** Ask for a new group's name. */
  async #askGroupName(value = "") {
    const input = el("input", { type: "text", value, placeholder: "e.g. Bedroom floors, Outlets", "aria-label": "Group name" });
    setTimeout(() => input.focus(), 0);
    const answer = await this._dialog("New group", [el("p", {}, "Measurements in a group share a colour and are added up together."), input],
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
    const answer = await this._dialog("Measurement", [el("label", { class: "rd-field" }, "Name", name), el("label", { class: "rd-field" }, "Group", group)],
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
    this._drawMeasures(m.page);
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
  /** Another document takes the viewer's place (another tab, or this one
   *  shown again after a change): the list of measurements goes, and comes
   *  back with its own document (see _loadMeasures). */
  _putAwayMeasures() {
    const tab = this.activeTab;
    if (!this.measPanel || !tab) return;
    tab.measListOpen = !this.measPanel.hidden;
    if (tab.measListOpen) this._toggleMeasPanel(false, false);
  }

  _loadMeasures() {
    const tab = this.activeTab, key = this.#measuresStore(tab);
    if (tab?.measListOpen) { tab.measListOpen = false; this._toggleMeasPanel(true, false); }
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
        ["Show", () => { this.toggleScaleBar(true); this._toggleMeasPanel(true); }]);
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
  async _measuresIntoPdf() {
    const tab = this.activeTab, items = tab?.measures || [];
    if (!items.length) return;
    const answer = await this._dialog("Put the measurements in the PDF", [el("p", {},
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
    await this._applyMarkup(changes, "Could not put the measurements in the PDF: ");
    if ((tab.version || 0) === version) return;  // it didn't work (and said so)
    this.#rememberMeasures();
    tab.measures = [];
    this.selected = null;
    this._drawAllMeasures();
    this.toast(`${items.length === 1 ? "The measurement is" : `${items.length} measurements are`} in the PDF now. Save to keep them.`, 4000);
  }

  async _exportMeasures() {
    const items = this.activeTab?.measures || [];
    if (!items.length) return;
    const cell = (c) => /[",\n]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : String(c);
    const text = "﻿" + this.#measureRows().map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";  // (the BOM: Excel reads it as UTF-8)
    const stem = this.name.replace(/\.pdf$/i, "");
    await this._saveBytes(new TextEncoder().encode(text), `${stem} measurements.csv`, () => {}, this.activeTab?.fileId);
  }

  #deleteMeasure(id) {
    const tab = this.activeTab;
    const m = tab?.measures?.find((x) => x.id === id);
    if (!m) return;
    this.#rememberMeasures();
    tab.measures = tab.measures.filter((x) => x !== m);
    if (this.selected?.id === id) this.selected = null;
    if (this.hot === id) this.hot = null;
    this._drawMeasures(m.page);
    this.#renderMeasPanel();
  }

  #hotMeasure(id) {
    const pages = new Set();
    for (const m of this.activeTab?.measures || []) if (m.id === id || m.id === this.hot) pages.add(m.page);
    this.hot = id;
    for (const n of pages) this._drawMeasures(n);
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
};
