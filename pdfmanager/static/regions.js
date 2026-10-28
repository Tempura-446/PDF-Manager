// Region editor: one page shown large, with rectangles drawn on it.
//
//   "crop"    one rectangle, the part of the page to keep (Crop margins).
//             Its edges and corners can be dragged.
//   "redact"  any number of black boxes per page (Redact). Drag on the page
//             to draw one; click a box to select it, drag to move, use the
//             corner handles to resize, Delete or × to remove. Text matches
//             found by the server are shown striped.
//
//   const ed = new RegionEditor(host, { mode, onChange(value) });
//   await ed.load(file, password)        // "ok", "password", "wrong-password" or "error"
//   ed.setCrop({ left, bottom, right, top })   // margins in points
//   ed.setMatches({ page: [[x0, y0, x1, y1]] })
// Coordinates sent back are PDF points (origin bottom left, before rotation):
// crop margins {left, bottom, right, top}; redact boxes {"1": [[x0, y0, x1, y1]]}
// with 1-based page numbers.

const BASE = new URL("/static/pdfjs/", location.href).href;
let pdfjs = null;
async function loadPdfjs() {
  if (!pdfjs) {
    pdfjs = await import(BASE + "build/pdf.min.mjs");
    pdfjs.GlobalWorkerOptions.workerSrc = BASE + "build/pdf.worker.min.mjs";
  }
  return pdfjs;
}

const ICON = {
  prev: '<path d="m15 18-6-6 6-6"/>', next: '<path d="m9 18 6-6-6-6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>', trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "i"); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICON[name];
  return s;
}
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

const HANDLES = { crop: ["n", "e", "s", "w", "ne", "nw", "se", "sw"], redact: ["ne", "nw", "se", "sw"] };
const MIN = 6;  // smallest box, in screen pixels

export class RegionEditor {
  constructor(host, { mode, onChange }) {
    this.mode = mode;
    this.onChange = onChange || (() => {});
    this.doc = null; this.task = null; this.file = null; this.password = null;
    this.pageIndex = 0; this.total = 0;
    this.crop = { left: 0, bottom: 0, right: 0, top: 0 };  // margins, points
    this.boxes = {};     // redact: page index -> [[x0, y0, x1, y1]] in PDF points
    this.matches = {};   // redact: page index -> boxes found by text search
    this.selected = -1;
    this.gen = 0;

    this.prevBtn = el("button", { type: "button", class: "chip-btn", title: "Previous page", "aria-label": "Previous page", onclick: () => this.goTo(this.pageIndex - 1) }, icon("prev"));
    this.nextBtn = el("button", { type: "button", class: "chip-btn", title: "Next page", "aria-label": "Next page", onclick: () => this.goTo(this.pageIndex + 1) }, icon("next"));
    this.pageLabel = el("span", { class: "re-page-label" });
    this.status = el("span", { class: "pb-status" });
    this.clearBtn = el("button", { type: "button", class: "btn secondary pb-small", onclick: () => this.#clearPage() }, icon("trash"), "Clear this page");
    this.toolbar = el("div", { class: "pb-toolbar" },
      el("div", { class: "re-nav" }, this.prevBtn, this.pageLabel, this.nextBtn), this.status,
      mode === "redact" ? this.clearBtn : null);
    this.canvas = el("canvas", { class: "re-canvas" });
    this.overlay = el("div", { class: "re-overlay", tabindex: "0", role: "application",
      "aria-label": mode === "crop" ? "Crop area: drag its edges" : "Page: drag to draw a box to black out" });
    this.stage = el("div", { class: "re-stage" }, this.canvas, this.overlay);
    this.message = el("div", { class: "pb-message" });
    this.root = el("div", { class: "region-editor", "data-mode": mode }, this.toolbar, this.stage, this.message);
    host.append(this.root);

    this.overlay.addEventListener("pointerdown", (e) => this.#down(e));
    this.overlay.addEventListener("keydown", (e) => this.#key(e));
    let width = 0;
    this.resize = new ResizeObserver(() => {
      const w = this.root.clientWidth;
      if (this.doc && Math.abs(w - width) > 20) { width = w; this.#show(); }
    });
    this.resize.observe(this.root);
    this.#state("empty");
  }

  async load(file, password = null) {
    if (this.file === file && this.password === password && this.doc) return "ok";
    await this.close();
    const gen = this.gen;
    this.file = file; this.password = password;
    this.#state("loading");
    const lib = await loadPdfjs();
    const data = new Uint8Array(await file.arrayBuffer());
    if (gen !== this.gen) return "stale";
    const task = lib.getDocument({ data, password: password || undefined, isEvalSupported: false,
      cMapUrl: BASE + "cmaps/", cMapPacked: true, standardFontDataUrl: BASE + "standard_fonts/",
      wasmUrl: BASE + "wasm/", iccUrl: BASE + "iccs/" });
    let locked = null;
    task.onPassword = (update, reason) => {
      locked = reason === lib.PasswordResponses.INCORRECT_PASSWORD ? "wrong-password" : "password";
      task.destroy();
    };
    this.task = task;
    try {
      this.doc = await task.promise;
    } catch (err) {
      if (gen !== this.gen) return "stale";
      this.task = null;
      this.#state(locked || "error", err.message);
      return locked || "error";
    }
    if (gen !== this.gen) return "stale";
    this.total = this.doc.numPages;
    this.pageIndex = 0;
    this.#state("ready");
    await this.#show();
    return "ok";
  }

  async close() {
    this.gen++;
    this.file = null; this.password = null; this.doc = null; this.total = 0;
    this.boxes = {}; this.matches = {}; this.selected = -1;
    const task = this.task;
    this.task = null;
    if (task) await task.destroy().catch(() => {});
    this.#state("empty");
  }

  /** Margins in points (from the tool's mm fields). */
  setCrop(margins) {
    this.crop = { ...margins };
    this.#draw();
  }

  setBoxes(boxes) {
    this.boxes = {};
    for (const [page, list] of Object.entries(boxes || {})) this.boxes[Number(page) - 1] = list.map((b) => [...b]);
    this.selected = -1;
    this.#draw();
  }

  setMatches(matches) {
    this.matches = {};
    for (const [page, list] of Object.entries(matches || {})) this.matches[Number(page) - 1] = list;
    this.#draw();
  }

  get boxCount() { return Object.values(this.boxes).reduce((n, list) => n + list.length, 0); }

  async goTo(index) {
    if (!this.doc || index < 0 || index >= this.total || index === this.pageIndex) return;
    this.pageIndex = index;
    this.selected = -1;
    await this.#show();
  }

  // ---- rendering --------------------------------------------------------------

  async #show() {
    const gen = this.gen, index = this.pageIndex;
    const page = await this.doc.getPage(index + 1);
    if (gen !== this.gen || index !== this.pageIndex) return;
    const base = page.getViewport({ scale: 1 });
    const width = Math.max(200, this.root.clientWidth - 2);
    const maxHeight = Math.max(320, Math.min(window.innerHeight * 0.7, 900));
    const scale = Math.min(width / base.width, maxHeight / base.height);
    this.viewport = page.getViewport({ scale });
    this.view = page.view;  // [x0, y0, x1, y1] of the crop box, PDF points
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = this.canvas;
    canvas.width = Math.floor(this.viewport.width * ratio);
    canvas.height = Math.floor(this.viewport.height * ratio);
    canvas.style.width = this.stage.style.width = `${Math.floor(this.viewport.width)}px`;
    canvas.style.height = this.stage.style.height = `${Math.floor(this.viewport.height)}px`;
    if (this.renderTask) this.renderTask.cancel();
    this.renderTask = page.render({ canvas, viewport: this.viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null });
    try { await this.renderTask.promise; } catch (e) { if (e?.name !== "RenderingCancelledException") throw e; }
    this.renderTask = null;
    this.#draw();
  }

  /** PDF box -> screen rectangle {x, y, w, h} (handles rotation). */
  #toScreen([x0, y0, x1, y1]) {
    const [ax, ay] = this.viewport.convertToViewportPoint(x0, y0);
    const [bx, by] = this.viewport.convertToViewportPoint(x1, y1);
    return { x: Math.min(ax, bx), y: Math.min(ay, by), w: Math.abs(bx - ax), h: Math.abs(by - ay) };
  }

  #toPdf({ x, y, w, h }) {
    const [ax, ay] = this.viewport.convertToPdfPoint(x, y);
    const [bx, by] = this.viewport.convertToPdfPoint(x + w, y + h);
    return [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)].map((v) => Math.round(v * 10) / 10);
  }

  #cropBox() {
    const [x0, y0, x1, y1] = this.view;
    const { left, bottom, right, top } = this.crop;
    return [x0 + left, y0 + bottom, x1 - right, y1 - top];
  }

  #draw() {
    if (!this.viewport) return;
    const parts = [];
    if (this.mode === "crop") {
      parts.push(this.#rectEl(this.#toScreen(this.#cropBox()), "re-crop", -1));
    } else {
      for (const m of this.matches[this.pageIndex] || []) {
        const r = this.#toScreen(m);
        parts.push(el("div", { class: "re-match", title: "Found by the words or kinds of numbers under Options", style: `left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px` }));
      }
      (this.boxes[this.pageIndex] || []).forEach((b, i) => parts.push(this.#rectEl(this.#toScreen(b), "re-box" + (i === this.selected ? " selected" : ""), i)));
    }
    this.overlay.replaceChildren(...parts);
    this.#update();
  }

  #rectEl(r, cls, index) {
    const box = el("div", { class: "re-rect " + cls, "data-index": index, style: `left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px` });
    if (this.mode === "crop" || index === this.selected) {
      for (const h of HANDLES[this.mode]) box.append(el("span", { class: `re-handle re-${h}`, "data-handle": h }));
    }
    if (this.mode === "redact" && index === this.selected) {
      box.append(el("button", { type: "button", class: "re-delete", title: "Remove this box", "aria-label": "Remove this box",
        onpointerdown: (e) => e.stopPropagation(), onclick: () => this.#remove(index) }, icon("x")));
    }
    return box;
  }

  // ---- interaction --------------------------------------------------------------

  #point(e) {
    const r = this.overlay.getBoundingClientRect();
    return { x: Math.max(0, Math.min(r.width, e.clientX - r.left)), y: Math.max(0, Math.min(r.height, e.clientY - r.top)) };
  }

  #down(e) {
    if (e.button !== 0 || !this.viewport) return;
    const handle = e.target.dataset.handle;
    const rectEl = e.target.closest(".re-rect");
    const start = this.#point(e);
    let kind, index = -1, origin;
    if (this.mode === "crop") {
      kind = handle || (rectEl ? "move" : null);
      if (!kind) return;
      origin = this.#toScreen(this.#cropBox());
    } else if (rectEl) {
      index = Number(rectEl.dataset.index);
      if (index !== this.selected) { this.selected = index; this.#draw(); }
      kind = handle || "move";
      origin = this.#toScreen(this.boxes[this.pageIndex][index]);
    } else {
      kind = "draw";
      this.selected = -1;
      origin = { x: start.x, y: start.y, w: 0, h: 0 };
    }
    e.preventDefault();
    this.overlay.focus({ preventScroll: true });
    this.overlay.setPointerCapture(e.pointerId);
    const bounds = { w: this.viewport.width, h: this.viewport.height };
    const move = (ev) => {
      const p = this.#point(ev), dx = p.x - start.x, dy = p.y - start.y;
      let { x, y, w, h } = origin;
      if (kind === "draw") {
        x = Math.min(start.x, p.x); y = Math.min(start.y, p.y); w = Math.abs(dx); h = Math.abs(dy);
      } else if (kind === "move") {
        x = Math.max(0, Math.min(bounds.w - w, x + dx)); y = Math.max(0, Math.min(bounds.h - h, y + dy));
      } else {
        let x1 = x + w, y1 = y + h;
        if (kind.includes("w")) x = Math.min(x1 - MIN, Math.max(0, x + dx));
        if (kind.includes("e")) x1 = Math.max(x + MIN, Math.min(bounds.w, x1 + dx));
        if (kind.includes("n")) y = Math.min(y1 - MIN, Math.max(0, y + dy));
        if (kind.includes("s")) y1 = Math.max(y + MIN, Math.min(bounds.h, y1 + dy));
        w = x1 - x; h = y1 - y;
      }
      this.#apply(kind, index, { x, y, w, h }, false);
    };
    const up = (ev) => {
      this.overlay.removeEventListener("pointermove", move);
      this.overlay.removeEventListener("pointerup", up);
      this.overlay.removeEventListener("pointercancel", up);
      move(ev);
      this.#apply(kind, index, null, true);
    };
    this.overlay.addEventListener("pointermove", move);
    this.overlay.addEventListener("pointerup", up);
    this.overlay.addEventListener("pointercancel", up);
  }

  #apply(kind, index, rect, done) {
    if (rect) {
      if (this.mode === "crop") {
        const [x0, y0, x1, y1] = this.#toPdf(rect), [vx0, vy0, vx1, vy1] = this.view;
        this.crop = { left: x0 - vx0, bottom: y0 - vy0, right: vx1 - x1, top: vy1 - y1 };
      } else {
        const list = this.boxes[this.pageIndex] ||= [];
        if (kind === "draw") {
          if (this.drawing == null) { list.push(this.#toPdf(rect)); this.drawing = list.length - 1; this.selected = this.drawing; }
          else list[this.drawing] = this.#toPdf(rect);
        } else list[index] = this.#toPdf(rect);
      }
      this.#draw();
    }
    if (done) {
      if (kind === "draw" && this.drawing != null) {
        const r = this.#toScreen(this.boxes[this.pageIndex][this.drawing]);
        if (r.w < MIN || r.h < MIN) { this.boxes[this.pageIndex].splice(this.drawing, 1); this.selected = -1; }
        this.drawing = null;
        this.#draw();
      }
      this.#emit();
    }
  }

  #remove(index) {
    (this.boxes[this.pageIndex] || []).splice(index, 1);
    this.selected = -1;
    this.#draw();
    this.#emit();
  }

  #clearPage() {
    if (!(this.boxes[this.pageIndex] || []).length) return;
    this.boxes[this.pageIndex] = [];
    this.selected = -1;
    this.#draw();
    this.#emit();
  }

  #key(e) {
    if (e.key === "PageDown" || (e.key === "ArrowRight" && e.altKey)) { e.preventDefault(); this.goTo(this.pageIndex + 1); return; }
    if (e.key === "PageUp" || (e.key === "ArrowLeft" && e.altKey)) { e.preventDefault(); this.goTo(this.pageIndex - 1); return; }
    if (this.mode === "redact" && this.selected >= 0 && (e.key === "Delete" || e.key === "Backspace")) { e.preventDefault(); this.#remove(this.selected); return; }
    if (this.mode === "redact" && e.key === "Escape") { this.selected = -1; this.#draw(); }
  }

  #emit() {
    if (this.mode === "crop") { this.onChange({ ...this.crop }); return; }
    const out = {};
    for (const [page, list] of Object.entries(this.boxes)) if (list.length) out[Number(page) + 1] = list;
    this.onChange(out);
  }

  // ---- status ----------------------------------------------------------------------

  #update() {
    this.pageLabel.textContent = this.total ? `Page ${this.pageIndex + 1} of ${this.total}` : "";
    this.prevBtn.disabled = this.pageIndex <= 0;
    this.nextBtn.disabled = this.pageIndex >= this.total - 1;
    if (this.mode === "crop") {
      const mm = (v) => Math.round((v * 25.4) / 72);
      const { top, right, bottom, left } = this.crop;
      this.status.textContent = `Trim: top ${mm(top)} · right ${mm(right)} · bottom ${mm(bottom)} · left ${mm(left)} mm`;
    } else {
      const here = (this.boxes[this.pageIndex] || []).length, found = (this.matches[this.pageIndex] || []).length;
      const all = this.boxCount, matches = Object.values(this.matches).reduce((n, l) => n + l.length, 0);
      const bits = [];
      if (here || found) bits.push(`this page: ${here ? here + " drawn" : ""}${here && found ? ", " : ""}${found ? found + " found" : ""}`);
      bits.push(`all pages: ${all} drawn, ${matches} found`);
      this.status.textContent = bits.join(" · ");
      this.clearBtn.disabled = !here;
    }
  }

  #state(name, detail) {
    this.root.dataset.state = name;
    this.toolbar.hidden = this.stage.hidden = name !== "ready";
    const text = {
      empty: "", loading: "Loading the page…",
      password: "This PDF is password-protected. Enter its password under Options to see the pages.",
      "wrong-password": "That password doesn't open this PDF. Check the password under Options.",
      error: `The page can't be shown${detail ? ": " + detail : "."}`,
    }[name];
    this.message.hidden = !text;
    this.message.textContent = text || "";
  }
}
