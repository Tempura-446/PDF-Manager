/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Region editor: one page shown large, with rectangles drawn on it.
//
//   "crop"    one rectangle, the part of the page to keep (Crop margins).
//             Its edges and corners can be dragged.
//   "redact"  any number of black boxes per page (Redact). Drag on the page
//             to draw one; click a box to select it, drag to move, use the
//             corner handles to resize, Delete or × to remove. Text matches
//             found by the server are shown striped.
//   "form"    fillable fields (Add form fields): drawn and edited like the
//             redact boxes, each with a type (text, multi-line text,
//             checkbox, radio button, dropdown, signature) and a name. A
//             click without dragging adds a field of the usual size. Radio
//             buttons with one name are a group, each with its own value;
//             a dropdown has its choices. Find fields (with a ``detect``
//             callback) adds the fields of a printed form. Fields already
//             in the file are shown striped.
//
//   const ed = new RegionEditor(host, { mode, onChange(value), detect() });
//   await ed.load(file, password)        // "ok", "password", "wrong-password" or "error"
//   ed.setCrop({ left, bottom, right, top })   // margins in points
//   ed.setMatches({ page: [[x0, y0, x1, y1]] })
//   ed.setExisting({ page: [{ rect, name, type }] })   // form fields in the file
// Coordinates sent back are PDF points (origin bottom left, before rotation):
// crop margins {left, bottom, right, top}; redact boxes {"1": [[x0, y0, x1, y1]]}
// with 1-based page numbers; form fields {"1": [{ rect, type, name, value?,
// options?, border? }]}.

import { DOC_OPTIONS, destroyTask, loadPdfjs, openDocument } from "./pdfjs.js";

const ICON = {
  prev: '<path d="m15 18-6-6 6-6"/>', next: '<path d="m9 18 6-6-6-6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  text: '<path d="M4 7V5h16v2M12 5v14M9 19h6"/>', multiline: '<path d="M4 6h16M4 10h16M4 14h16M4 18h10"/>',
  checkbox: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="m8 12 3 3 5-6"/>',
  radio: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/>',
  dropdown: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="m13 11 2.5 2.5L18 11"/>',
  find: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/><path d="M8.5 11h5M11 8.5v5"/>',
  signature: '<path d="M3 17c3-1 4-9 7-9 2 0 0 7 2 7s2-3 4-3 2 2 5 2"/><path d="M3 21h18"/>', trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
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

const HANDLES = { crop: ["n", "e", "s", "w", "ne", "nw", "se", "sw"], redact: ["ne", "nw", "se", "sw"], form: ["ne", "nw", "se", "sw"] };

/** Crop margins {left, bottom, right, top} of a page turned by ``degrees``
 * (its /Rotate) -> the same edges as the page is shown; a negative angle
 * goes back from shown edges to the page's own. */
function turnMargins({ left, bottom, right, top }, degrees) {
  switch (((degrees % 360) + 360) % 360) {
    case 90: return { left: bottom, bottom: right, right: top, top: left };
    case 180: return { left: right, bottom: top, right: left, top: bottom };
    case 270: return { left: top, bottom: left, right: bottom, top: right };
    default: return { left, bottom, right, top };
  }
}
const MIN = 6;  // smallest box, in screen pixels

/** The share of the smaller of two boxes [x0, y0, x1, y1] they have in common. */
function overlapShare(a, b) {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]), h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  if (w <= 0 || h <= 0) return 0;
  const smaller = Math.min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]));
  return smaller > 0 ? (w * h) / smaller : 0;
}

// Form fields: label, and the size (points) of one added with a click.
export const FIELD_TYPES = {
  text: ["Text", 180, 20], multiline: ["Multi-line text", 260, 64],
  checkbox: ["Checkbox", 12, 12], radio: ["Radio button", 12, 12],
  dropdown: ["Dropdown", 140, 20], signature: ["Signature", 180, 40],
};
const FIELD_LABEL = { text: "Text", multiline: "Text", checkbox: "Checkbox", radio: "Group", dropdown: "Dropdown", signature: "Signature" };
const AUTO_NAME = /^(Text|Checkbox|Group|Dropdown|Signature) \d+$/;

export class RegionEditor {
  constructor(host, { mode, onChange, detect }) {
    this.mode = mode;
    this.onChange = onChange || (() => {});
    this.detect = detect || null;
    this.doc = null; this.task = null; this.file = null; this.password = null;
    this.pageIndex = 0; this.total = 0;
    this.crop = { left: 0, bottom: 0, right: 0, top: 0 };  // margins, points, in the page's own coordinates
    this.rotation = 0;  // the shown page's /Rotate
    this.boxes = {};     // redact: page index -> [[x0, y0, x1, y1]] in PDF points
    this.matches = {};   // redact: page index -> boxes found by text search
    this.kinds = {};     // form: page index -> [{type, name}], one per box
    this.existing = {};  // form: page index -> fields already in the file
    this.fieldType = "text";
    this.selected = -1;
    this.gen = 0;

    this.prevBtn = el("button", { type: "button", class: "chip-btn", title: "Previous page", "aria-label": "Previous page", onclick: () => this.goTo(this.pageIndex - 1) }, icon("prev"));
    this.nextBtn = el("button", { type: "button", class: "chip-btn", title: "Next page", "aria-label": "Next page", onclick: () => this.goTo(this.pageIndex + 1) }, icon("next"));
    this.pageLabel = el("span", { class: "re-page-label" });
    this.status = el("span", { class: "pb-status" });
    this.clearBtn = el("button", { type: "button", class: "btn secondary pb-small", onclick: () => this.#clearPage() }, icon("trash"), "Clear this page");
    this.findBtn = mode === "form" && this.detect ? el("button", { type: "button", class: "btn secondary pb-small",
      title: "Add fields where the form has lines, boxes and tick boxes to fill in", onclick: () => this.#find() }, icon("find"), "Find fields") : null;
    this.toolbar = el("div", { class: "pb-toolbar" },
      el("div", { class: "re-nav" }, this.prevBtn, this.pageLabel, this.nextBtn), this.status,
      this.findBtn, mode !== "crop" ? this.clearBtn : null);
    if (mode === "form") this.#buildFieldBar();
    this.canvas = el("canvas", { class: "re-canvas" });
    this.overlay = el("div", { class: "re-overlay", tabindex: "0", role: "application",
      "aria-label": { crop: "Crop area: drag its edges", redact: "Page: drag to draw a box to black out",
        form: "Page: drag or click to add a form field" }[mode] });
    this.stage = el("div", { class: "re-stage" }, this.canvas, this.overlay);
    this.message = el("div", { class: "pb-message" });
    this.root = el("div", { class: "region-editor", "data-mode": mode }, this.toolbar, this.fieldBar, this.stage, this.message);
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
    const task = await openDocument({ ...DOC_OPTIONS, data, password: password || undefined });
    let locked = null;
    task.onPassword = (update, reason) => {
      locked = reason === lib.PasswordResponses.INCORRECT_PASSWORD ? "wrong-password" : "password";
      destroyTask(task);
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
    this.boxes = {}; this.matches = {}; this.kinds = {}; this.existing = {}; this.selected = -1;
    const task = this.task;
    this.task = null;
    await destroyTask(task);
    this.#state("empty");
  }

  /** Margins in points (from the tool's mm fields), as the page is shown:
   * "top" is the top of the page as it's turned by its /Rotate. */
  setCrop(margins) {
    this.crop = turnMargins(margins, -this.rotation);  // into the page's own coordinates
    this.#draw();
  }

  setBoxes(boxes) {
    this.boxes = {}; this.kinds = {};
    for (const [page, list] of Object.entries(boxes || {})) {
      this.boxes[Number(page) - 1] = list.map((b) => [...(b.rect || b)]);
      if (this.mode === "form") this.kinds[Number(page) - 1] = list.map(({ rect, ...b }) => ({ ...b, type: b.type || "text", name: b.name || "" }));
    }
    this.selected = -1;
    this.#draw();
  }

  /** Form fields already in the file: {page: [{rect, name, type}]}. */
  setExisting(fields) {
    this.existing = {};
    for (const [page, list] of Object.entries(fields || {})) this.existing[Number(page) - 1] = list;
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
    const gen = this.gen, index = this.pageIndex, ticket = this.showing = (this.showing || 0) + 1;
    const page = await this.doc.getPage(index + 1);
    if (gen !== this.gen || index !== this.pageIndex) return;
    if (this.renderTask) {
      // A canvas can only be drawn by one render at a time.
      this.renderTask.cancel();
      await this.renderTask.promise.catch(() => {});
    }
    if (ticket !== this.showing) return;  // a newer call takes over
    const base = page.getViewport({ scale: 1 });
    const width = Math.max(200, this.root.clientWidth - 2);
    const maxHeight = Math.max(320, Math.min(window.innerHeight * 0.7, 900));
    const scale = Math.min(width / base.width, maxHeight / base.height);
    this.viewport = page.getViewport({ scale });
    this.view = page.view;  // [x0, y0, x1, y1] of the crop box, PDF points
    if (this.mode === "crop" && this.rotation !== page.rotate) {
      // The margins were set for the previous page's turn.
      this.crop = turnMargins(turnMargins(this.crop, this.rotation), -page.rotate);
    }
    this.rotation = page.rotate;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = this.canvas;
    canvas.width = Math.floor(this.viewport.width * ratio);
    canvas.height = Math.floor(this.viewport.height * ratio);
    canvas.style.width = this.stage.style.width = `${Math.floor(this.viewport.width)}px`;
    canvas.style.height = this.stage.style.height = `${Math.floor(this.viewport.height)}px`;
    this.renderTask = page.render({ canvas, viewport: this.viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null });
    const task = this.renderTask;
    try { await task.promise; } catch (e) { if (e?.name !== "RenderingCancelledException") throw e; }
    if (this.renderTask === task) this.renderTask = null;
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
    } else if (this.mode === "form") {
      for (const f of this.existing[this.pageIndex] || []) {
        const r = this.#toScreen(f.rect);
        parts.push(el("div", { class: "re-existing", title: `Already in the file: ${f.name}`, style: `left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px` }));
      }
      const kinds = this.kinds[this.pageIndex] || [];
      (this.boxes[this.pageIndex] || []).forEach((b, i) => {
        const box = this.#rectEl(this.#toScreen(b), "re-field" + (i === this.selected ? " selected" : ""), i);
        const kind = kinds[i] || { type: "text", name: "" };
        box.dataset.type = kind.type;
        box.title = `${FIELD_TYPES[kind.type]?.[0] || kind.type}: ${kind.name}${kind.type === "radio" ? ` = ${kind.value || ""}` : ""}`;
        box.prepend(el("span", { class: "re-field-label" }, kind.type === "checkbox" ? "" : kind.type === "radio" ? "" : kind.name));
        parts.push(box);
      });
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
    if (this.mode !== "crop" && index === this.selected) {
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
      this.drawStart = start;
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
          if (this.drawing == null) {
            list.push(this.#toPdf(rect)); this.drawing = list.length - 1; this.selected = this.drawing;
            if (this.mode === "form") (this.kinds[this.pageIndex] ||= []).push(this.#newKind(this.fieldType));
          } else list[this.drawing] = this.#toPdf(rect);
        } else list[index] = this.#toPdf(rect);
      }
      this.#draw();
    }
    if (done) {
      this.findNote = "";
      if (kind === "draw" && this.mode === "form" && (this.drawing == null || this.#tooSmall(this.drawing))) {
        // A click: a field of the usual size, with its top left corner there.
        if (this.drawing != null) this.#splice(this.drawing);
        const [, w, h] = FIELD_TYPES[this.fieldType], s = this.viewport.scale;
        const size = { w: w * s, h: h * s }, p = this.drawStart;
        const small = this.fieldType === "checkbox" || this.fieldType === "radio";
        let x = small ? p.x - size.w / 2 : p.x, y = small ? p.y - size.h / 2 : p.y - size.h * 0.75;
        x = Math.max(0, Math.min(this.viewport.width - size.w, x)); y = Math.max(0, Math.min(this.viewport.height - size.h, y));
        (this.boxes[this.pageIndex] ||= []).push(this.#toPdf({ x, y, ...size }));
        (this.kinds[this.pageIndex] ||= []).push(this.#newKind(this.fieldType));
        this.selected = this.boxes[this.pageIndex].length - 1;
        this.drawing = null;
        this.#draw();
      } else if (kind === "draw" && this.drawing != null) {
        if (this.#tooSmall(this.drawing)) { this.#splice(this.drawing); this.selected = -1; }
        this.drawing = null;
        this.#draw();
      }
      this.#emit();
    }
  }

  #tooSmall(index) {
    const r = this.#toScreen(this.boxes[this.pageIndex][index]);
    return r.w < MIN || r.h < MIN;
  }

  #splice(index) {
    (this.boxes[this.pageIndex] || []).splice(index, 1);
    (this.kinds[this.pageIndex] || []).splice(index, 1);
  }

  #remove(index) {
    this.#splice(index);
    this.selected = -1;
    this.#draw();
    this.#emit();
  }

  #clearPage() {
    this.findNote = "";
    if (!(this.boxes[this.pageIndex] || []).length) return;
    this.boxes[this.pageIndex] = [];
    this.kinds[this.pageIndex] = [];
    this.selected = -1;
    this.#draw();
    this.#emit();
  }

  #key(e) {
    if (e.key === "PageDown" || (e.key === "ArrowRight" && e.altKey)) { e.preventDefault(); this.goTo(this.pageIndex + 1); return; }
    if (e.key === "PageUp" || (e.key === "ArrowLeft" && e.altKey)) { e.preventDefault(); this.goTo(this.pageIndex - 1); return; }
    if (this.mode !== "crop" && this.selected >= 0 && (e.key === "Delete" || e.key === "Backspace")) { e.preventDefault(); this.#remove(this.selected); return; }
    if (this.mode !== "crop" && e.key === "Escape") { this.selected = -1; this.#draw(); }
  }

  #emit() {
    if (this.mode === "crop") { this.onChange(turnMargins(this.crop, this.rotation)); return; }
    const out = {};
    for (const [page, list] of Object.entries(this.boxes)) {
      if (!list.length) continue;
      out[Number(page) + 1] = this.mode === "form" ? list.map((rect, i) => ({ rect, ...this.kinds[page][i] })) : list;
    }
    this.onChange(out);
  }

  // ---- form fields ----------------------------------------------------------------

  #buildFieldBar() {
    this.typeBtns = Object.entries(FIELD_TYPES).map(([type, [label]]) => el("button", {
      type: "button", class: "re-type", "data-type": type, title: `Add ${label.toLowerCase()} fields`,
      "aria-pressed": String(type === this.fieldType), onclick: () => this.#setType(type),
    }, icon(type), el("span", {}, label)));
    this.nameInput = el("input", { type: "text", class: "re-name", spellcheck: "false", "aria-label": "Field name",
      oninput: () => this.#changeSelected({ name: this.nameInput.value }),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); this.overlay.focus({ preventScroll: true }); } },
    });
    this.typeSelect = el("select", { class: "re-name", "aria-label": "Type of this field",
      onchange: () => {
        const kind = this.kinds[this.pageIndex]?.[this.selected], type = this.typeSelect.value;
        if (!kind) return;
        // An automatic name follows the type ("Text 2" -> "Checkbox 1").
        const name = AUTO_NAME.test(kind.name) ? (kind.name = "", this.#nextName(type)) : kind.name;
        const changes = { type, name };
        if (type === "radio" && !kind.value) changes.value = this.#nextValue(name);
        this.#changeSelected(changes);
      },
    }, ...Object.entries(FIELD_TYPES).map(([type, [label]]) => el("option", { value: type }, label)));
    this.valueInput = el("input", { type: "text", class: "re-name re-short", spellcheck: "false", "aria-label": "This button's value",
      oninput: () => this.#changeSelected({ value: this.valueInput.value }),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); this.overlay.focus({ preventScroll: true }); } },
    });
    this.optionsInput = el("input", { type: "text", class: "re-name re-short", spellcheck: "false", placeholder: "Yes, No, Maybe",
      "aria-label": "Choices, separated by commas",
      oninput: () => this.#changeSelected({ options: this.optionsInput.value }),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); this.overlay.focus({ preventScroll: true }); } },
    });
    this.nameLabel = el("span", {}, "Name");
    // Text fields: a format (Acrobat's: numbers, money, dates…) and a total.
    const lang = (navigator.language || "en").toLowerCase();
    const symbol = lang === "en-gb" ? "£" : /^en(-us|-ca|-au)?$|^es-(mx|us)/.test(lang) ? "$" : "€";
    const datePattern = /^en(-us)?$/.test(lang) ? "mm/dd/yyyy" : lang.startsWith("ja") || lang.startsWith("zh") ? "yyyy/mm/dd" : "dd/mm/yyyy";
    this.formats = { "": {}, number: { format: "number" }, amount: { format: "amount" },
      currency: { format: "currency", currency: symbol }, percent: { format: "percent" },
      date: { format: "date", date: datePattern }, phone: { format: "phone" }, zip: { format: "zip" } };
    this.formatSelect = el("select", { class: "re-name", "aria-label": "What can be typed in this field",
      onchange: () => this.#changeSelected({ format: undefined, currency: undefined, date: undefined, ...this.formats[this.formatSelect.value] }),
    }, ...[["", "Any text"], ["number", "Number"], ["amount", "Amount (1,234.56)"], ["currency", `Money (${symbol})`],
      ["percent", "Percent"], ["date", `Date (${datePattern})`], ["phone", "Phone number (US)"], ["zip", "ZIP code (US)"]]
      .map(([v, label]) => el("option", { value: v }, label)));
    this.sumInput = el("input", { type: "text", class: "re-name re-short", spellcheck: "false", placeholder: "Price, Tax",
      "aria-label": "Adds up these fields (their names, separated by commas)",
      oninput: () => this.#changeSelected({ calculate: this.sumInput.value.trim() ? "sum: " + this.sumInput.value : "" }),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); this.overlay.focus({ preventScroll: true }); } },
    });
    this.textLabel = el("span", { class: "re-textopts" },
      el("label", { title: "Numbers, money and dates are checked and shown in their format as people type" }, el("span", {}, "Format"), this.formatSelect),
      el("label", { title: "Fill this field in by itself with the total of other fields" }, el("span", {}, "Sum of"), this.sumInput));
    this.valueLabel = el("label", { title: "What this button gives when it's chosen" }, el("span", {}, "Value"), this.valueInput);
    this.optionsLabel = el("label", { title: "The choices in the list, separated by commas" }, el("span", {}, "Choices"), this.optionsInput);
    // One slot for a radio button's value or a dropdown's choices, which
    // keeps its space: the page mustn't move while a field is clicked.
    this.extraSlot = el("div", { class: "re-extra" }, this.valueLabel, this.optionsLabel, this.textLabel);
    this.nameBox = el("div", { class: "re-name-box" },
      el("label", {}, this.nameLabel, this.nameInput), el("label", {}, el("span", {}, "Type"), this.typeSelect),
      this.extraSlot);
    this.fieldBar = el("div", { class: "re-fieldbar" },
      el("div", { class: "re-add" }, el("span", { class: "re-add-label" }, "Add"),
        el("div", { class: "re-types", role: "group", "aria-label": "Type of field to add" }, ...this.typeBtns)),
      this.nameBox);
  }

  /** The type of field added by the next drag or click. */
  #setType(type) {
    this.fieldType = type;
    if (this.selected >= 0) { this.selected = -1; this.#draw(); }
    this.#syncFieldBar();
  }

  #changeSelected(changes) {
    const kind = this.kinds[this.pageIndex]?.[this.selected];
    if (!kind) return;
    Object.assign(kind, changes);
    this.#draw();
    this.#emit();
  }

  #syncFieldBar() {
    if (!this.fieldBar) return;
    const kind = this.kinds[this.pageIndex]?.[this.selected];
    for (const b of this.typeBtns) b.setAttribute("aria-pressed", String(b.dataset.type === this.fieldType));
    // Hidden but keeping its space, so the page doesn't jump while drawing.
    this.nameBox.style.visibility = kind ? "" : "hidden";
    this.nameBox.inert = !kind;
    if (!kind) return;
    if (document.activeElement !== this.nameInput) this.nameInput.value = kind.name;
    this.typeSelect.value = kind.type;
    this.nameLabel.textContent = kind.type === "radio" ? "Group" : "Name";
    this.nameInput.setAttribute("aria-label", kind.type === "radio" ? "Group name (buttons with the same one are a group)" : "Field name");
    this.valueLabel.hidden = kind.type !== "radio";
    this.optionsLabel.hidden = kind.type !== "dropdown";
    const text = kind.type === "text" || kind.type === "multiline";
    this.textLabel.hidden = !text;
    this.extraSlot.style.visibility = kind.type === "radio" || kind.type === "dropdown" || text ? "" : "hidden";
    if (text) {
      this.formatSelect.value = kind.format ? (Object.keys(this.formats).find((k) => this.formats[k].format === kind.format) || "") : "";
      if (document.activeElement !== this.sumInput) this.sumInput.value = String(kind.calculate || "").replace(/^\s*sum\s*:\s*/i, "");
    }
    if (document.activeElement !== this.valueInput) this.valueInput.value = kind.value || "";
    if (document.activeElement !== this.optionsInput) {
      this.optionsInput.value = Array.isArray(kind.options) ? kind.options.join(", ") : kind.options || "";
    }
  }

  /** The type and name of a field about to be added. Radio buttons added
   * one after another join the same group. */
  #newKind(type) {
    if (type !== "radio") return { type, name: this.#nextName(type) };
    const last = this.lastRadio && Object.values(this.kinds).flat().find((k) => k === this.lastRadio);
    const name = last ? last.name : this.#nextName("radio");
    const kind = { type, name, value: this.#nextValue(name) };
    this.lastRadio = kind;
    return kind;
  }

  /** "Choice 1", "Choice 2"... not used in the group yet. */
  #nextValue(group) {
    const taken = new Set(Object.values(this.kinds).flat().filter((k) => k.type === "radio" && k.name === group).map((k) => k.value));
    let n = 1;
    while (taken.has(`Choice ${n}`)) n++;
    return `Choice ${n}`;
  }

  /** Add the fields of a printed form (the server finds them). */
  async #find() {
    if (!this.detect || this.finding) return;
    this.finding = true;
    this.findBtn.disabled = true;
    const label = this.findBtn.lastChild;
    label.textContent = "Looking…";
    try {
      const found = await this.detect();
      let added = 0, firstPage = null;
      for (const [page, list] of Object.entries(found || {})) {
        const index = Number(page) - 1;
        const boxes = this.boxes[index] ||= [], kinds = this.kinds[index] ||= [];
        for (const { rect, ...kind } of list) {
          if (boxes.some((b) => overlapShare(b, rect) > 0.3)) continue;  // drawn already
          boxes.push([...rect]);
          kinds.push({ ...kind, name: this.#freeName(kind.name, kind.type, kinds) });
          added++;
          if (firstPage == null || index < firstPage) firstPage = index;
        }
      }
      this.selected = -1;
      this.findNote = added
        ? `Found ${added} field${added === 1 ? "" : "s"}: check them, and click one to rename it, change its type or remove it.`
        : "No fields found: the form needs lines, boxes or tick boxes to fill in. Draw the fields instead.";
      if (added && firstPage != null && firstPage !== this.pageIndex && !(this.boxes[this.pageIndex] || []).length) await this.goTo(firstPage);
      else this.#draw();
      this.#emit();
    } catch (err) {
      this.findNote = `Fields can't be found: ${err.message || err}`;
      this.#update();
    } finally {
      this.finding = false;
      this.findBtn.disabled = false;
      label.textContent = "Find fields";
    }
  }

  /** The found name, unless a field drawn here already has it (radio
   * buttons share their group's name). */
  #freeName(name, type, siblings) {
    if (type === "radio") return name;
    const taken = new Set(Object.values(this.kinds).flat().map((k) => k.name));
    if (!taken.has(name)) return name;
    let n = 2;
    while (taken.has(`${name} ${n}`)) n++;
    return `${name} ${n}`;
  }

  /** "Text 1", "Text 2"... not used by another field yet. */
  #nextName(type) {
    const taken = new Set();
    for (const list of Object.values(this.kinds)) for (const k of list) taken.add(k.name);
    for (const list of Object.values(this.existing)) for (const f of list) taken.add(f.name);
    let n = 1;
    while (taken.has(`${FIELD_LABEL[type]} ${n}`)) n++;
    return `${FIELD_LABEL[type]} ${n}`;
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
    } else if (this.mode === "form") {
      const here = (this.boxes[this.pageIndex] || []).length, all = this.boxCount;
      const old = Object.values(this.existing).reduce((n, l) => n + l.length, 0);
      const count = (n) => `${n} field${n === 1 ? "" : "s"}`;
      this.status.textContent = `${here !== all ? `this page: ${count(here)} · ` : ""}new: ${count(all)}${old ? ` · already in the file: ${old}` : ""}`;
      if (this.findNote) this.status.textContent += ` — ${this.findNote}`;
      this.clearBtn.disabled = !here;
      this.#syncFieldBar();
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
    if (this.fieldBar) this.fieldBar.hidden = name !== "ready";
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
