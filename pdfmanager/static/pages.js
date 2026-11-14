/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Page board: a preview of every page of one PDF, used by the page tools.
// What the user does with the pages is shown to them (and sent to the
// server) as a page range such as "3,1-2,4-10". Modes:
//
//   "order"   drag pages into a new order (Reorder pages). Pages can also be
//             moved with the keyboard (arrow keys pick a page, Alt+arrow keys
//             move it), duplicated and removed.
//   "select"  click pages to pick them (Extract, Delete, Rotate). The effect
//             ("keep", "remove" or "rotate") decides how picked pages look.
//   "view"    a read-only preview of the result (Reverse order).
//   "split"   click a page to start a new file there (Split).
//
//   const board = new PageBoard(host, { onChange(pages) {} });
//   board.configure({ mode: "select", effect: "remove" })
//   await board.load(file, password)   // "ok", "password" or "wrong-password"
//   board.order / board.selection      // 0-based page indices
//   board.setOrder([2, 0, 1]) / board.setSelection([0, 4])   // e.g. from a typed page range
//   board.splits / board.setSplits([3, 7])   // 0-based pages that start a new file

import { DOC_OPTIONS, destroyTask, loadPdfjs, openDocument } from "./pdfjs.js";

const THUMB = 132;   // CSS pixels for the longest side of a preview
const RENDERERS = 2; // pages rendered at the same time

const ICON = {
  left: '<path d="m15 18-6-6 6-6"/>', right: '<path d="m9 18 6-6-6-6"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>', check: '<path d="M20 6 9 17l-5-5"/>',
  cw: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  reverse: '<path d="M4 7h13l-3-3M20 17H7l3 3"/>',
  reset: '<path d="M4 11a8 8 0 1 1 2.3 5.7"/><path d="M4 4v7h7"/>',
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

// ---- page ranges (the same syntax as pdfmanager/pageranges.py) ---------------

/** 0-based indices for a spec like "3,1-2,odd", or null if it isn't valid. */
export function parseRanges(spec, total) {
  const num = (t) => {
    t = t.trim();
    if (t === "end" || t === "last") return total;
    if (!/^\d+$/.test(t)) return null;
    const n = Number(t);
    return n >= 1 && n <= total ? n : null;
  };
  const out = [];
  for (let part of (spec || "").split(",")) {
    part = part.trim().toLowerCase();
    if (!part) continue;
    if (part === "all" || part === "*") for (let i = 0; i < total; i++) out.push(i);
    else if (part === "odd") for (let i = 0; i < total; i += 2) out.push(i);
    else if (part === "even") for (let i = 1; i < total; i += 2) out.push(i);
    else if (part.includes("-")) {
      const [a, b] = part.split(/-(.*)/s);
      const start = a.trim() ? num(a) : 1, end = b.trim() ? num(b) : total;
      if (start === null || end === null) return null;
      const step = start <= end ? 1 : -1;
      for (let i = start; i !== end + step; i += step) out.push(i - 1);
    } else {
      const n = num(part);
      if (n === null) return null;
      out.push(n - 1);
    }
  }
  return out.length ? out : null;
}

/** The shortest page range for 0-based indices: [2,0,1,3,4,5] -> "3,1-2,4-6". */
export function formatRanges(order) {
  const parts = [];
  for (let i = 0; i < order.length;) {
    const step = order[i + 1] - order[i];
    let j = i;
    if (step === 1 || step === -1) while (j + 1 < order.length && order[j + 1] - order[j] === step) j++;
    parts.push(j > i + 1 ? `${order[i] + 1}-${order[j] + 1}` : String(order[i] + 1));
    if (j === i + 1) parts.push(String(order[j] + 1));
    i = j + 1;
  }
  return parts.join(",");
}

// ---- the board ------------------------------------------------------------------

export class PageBoard {
  constructor(host, { onChange } = {}) {
    this.onChange = onChange || (() => {});
    this.file = null; this.password = null;
    this.doc = null; this.task = null; this.total = 0;
    this.gen = 0;                // bumped on every load/close; stale renders check it
    this.thumbs = new Map();     // page index -> object URL of its preview
    this.rendering = new Map();  // page index -> promise
    this.queue = []; this.active = 0;
    this.history = [];
    this.seq = 0;
    this.drag = null;
    this.mode = "order"; this.effect = null; this.rotation = 90;
    this.picked = new Set(); this.anchor = null;
    this.starts = new Set();  // split mode: pages that start a new file

    const button = (label, fn, name, title) => el("button", { type: "button", class: "btn secondary pb-small", title, onclick: fn }, name ? icon(name) : null, label);
    this.status = el("span", { class: "pb-status" });
    this.undoBtn = button("Undo", () => this.undo(), "undo", "Undo (Ctrl+Z)");
    this.orderTools = el("div", { class: "pb-tools" }, this.undoBtn,
      button("Reverse", () => this.#change(this.order.reverse()), "reverse"),
      button("Reset", () => this.#change([...Array(this.total).keys()]), "reset"));
    const pick = (fn) => () => this.#pick(this.#all().filter(fn));
    this.selectTools = el("div", { class: "pb-tools" },
      button("All", pick(() => true), null, "Select all pages (Ctrl+A)"),
      button("Odd", pick((p) => p % 2 === 0), null, "Select pages 1, 3, 5…"),
      button("Even", pick((p) => p % 2 === 1), null, "Select pages 2, 4, 6…"),
      button("Invert", () => this.#pick(this.#all().filter((p) => !this.picked.has(p))), null, "Swap selected and unselected pages"),
      button("Clear", pick(() => false), null, "Select no pages"));
    this.splitTools = el("div", { class: "pb-tools" },
      button("Every page", () => this.#split(this.#all().slice(1)), null, "One file per page"),
      button("Clear", () => this.#split([]), null, "Keep everything in one file"));
    this.toolbar = el("div", { class: "pb-toolbar" }, this.status, this.orderTools, this.selectTools, this.splitTools);
    this.grid = el("div", { class: "pb-grid", role: "list", "aria-label": "Pages in their new order" });
    this.message = el("div", { class: "pb-message" });
    this.live = el("div", { class: "pb-live", "aria-live": "polite" });
    this.root = el("div", { class: "page-board" }, this.toolbar, this.grid, this.message, this.live);
    host.append(this.root);

    this.observer = new IntersectionObserver((entries) => {
      for (const e of entries) {
        e.target.dataset.visible = e.isIntersecting ? "1" : "";
        if (e.isIntersecting) this.#want(e.target);
      }
    }, { root: this.grid, rootMargin: "200px 0px" });

    this.grid.addEventListener("pointerdown", (e) => this.#pointerDown(e));
    this.grid.addEventListener("click", (e) => this.#click(e));
    this.grid.addEventListener("keydown", (e) => this.#keyDown(e));
    this.grid.addEventListener("focusin", (e) => { const t = e.target.closest(".pb-tile"); if (t) this.#rove(t); });
    this.configure({ mode: "order" });
    this.#state("empty");
  }

  get order() { return [...this.grid.children].map((t) => Number(t.dataset.page)); }
  get selection() { return [...this.picked].sort((a, b) => a - b); }

  /** Switch mode ("order", "select" or "view") and effect; the open PDF is kept.
   *  Every switch starts again from the original page order and no selection. */
  configure({ mode, effect = null, onChange } = {}) {
    if (onChange) this.onChange = onChange;
    this.#endDrag(false);
    this.mode = mode; this.effect = effect;
    this.root.dataset.mode = mode;
    this.root.dataset.effect = effect || "";
    this.orderTools.hidden = mode !== "order";
    this.selectTools.hidden = mode !== "select";
    this.splitTools.hidden = mode !== "split";
    this.grid.setAttribute("aria-label", { order: "Pages in their new order", select: "Pages; select the ones to use", view: "Pages in the order they will have", split: "Pages; click a page to start a new file there" }[mode]);
    this.grid.setAttribute("aria-multiselectable", mode === "select" ? "true" : "false");
    this.picked.clear(); this.anchor = null; this.history = []; this.starts.clear();
    if (this.doc) {
      const pages = [...Array(this.total).keys()];
      this.#build(mode === "view" && effect === "reverse" ? pages.reverse() : pages);
      this.#update();
    }
  }

  get splits() { return [...this.starts].sort((a, b) => a - b); }

  /** Start new files at these pages (0-based) without reporting it back. */
  setSplits(pages) {
    if (!this.doc) return false;
    this.starts = new Set(pages.filter((p) => p > 0 && p < this.total));
    this.#update();
    return true;
  }

  #split(pages) {
    const before = this.splits.join();
    this.starts = new Set(pages.filter((p) => p > 0 && p < this.total));
    this.#update();
    if (this.splits.join() !== before) this.onChange(this.splits);
  }

  /** Select these pages (0-based) without reporting it back. */
  setSelection(pages) {
    if (!this.doc) return false;
    this.picked = new Set(pages.filter((p) => p >= 0 && p < this.total));
    this.#update();
    return true;
  }

  /** How far "rotate" turns the picked pages, in degrees clockwise. */
  setRotation(degrees) {
    this.rotation = ((Number(degrees) || 0) % 360 + 360) % 360;
    this.root.style.setProperty("--pb-turn", this.rotation + "deg");
    this.root.style.setProperty("--pb-fit", this.rotation % 180 ? "0.8" : "1");
    this.#update();
  }

  /** Open a PDF. Returns "ok", or "password" / "wrong-password" when it is protected. */
  load(file, password = null) {
    const w = this.wanted;
    if (w && w.file === file && w.password === password) return w.loading;
    this.wanted = { file, password, loading: this.#load(file, password) };
    return this.wanted.loading;
  }

  async #load(file, password) {
    await this.#close();
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
      if (locked) { this.#state(locked); return locked; }
      this.#state("error", err.message);
      return "error";
    }
    if (gen !== this.gen) return "stale";
    this.total = this.doc.numPages;
    this.#state("ready");
    this.configure({ mode: this.mode, effect: this.effect });
    return "ok";
  }

  async close() {
    this.wanted = null;
    return this.#close();
  }

  async #close() {
    this.gen++;
    this.#endDrag(false);
    this.observer.disconnect();
    this.grid.replaceChildren();
    for (const url of this.thumbs.values()) URL.revokeObjectURL(url);
    this.thumbs.clear(); this.rendering.clear(); this.queue = [];
    this.file = null; this.password = null; this.doc = null; this.total = 0;
    const task = this.task;
    this.task = null;
    await destroyTask(task);
    this.#state("empty");
  }

  /** Show a new order (0-based indices, repeats allowed) without reporting it back. */
  setOrder(order) {
    if (!this.doc || order.some((p) => !(p >= 0 && p < this.total))) return false;
    if (order.join() !== this.order.join()) this.#build(order);
    this.#update();
    return true;
  }

  undo() {
    if (!this.history.length) return;
    this.#build(this.history.pop());
    this.#update();
    this.onChange(this.order);
  }

  // ---- building and rendering ------------------------------------------------

  #build(order) {
    this.observer.disconnect();
    this.grid.setAttribute("role", this.mode === "select" ? "listbox" : "list");
    this.grid.replaceChildren(...order.map((p) => this.#tile(p)));
    for (const t of this.grid.children) this.observer.observe(t);
  }

  #tile(page) {
    const thumb = el("div", { class: "pb-thumb" });
    const url = this.thumbs.get(page);
    thumb.append(url ? el("img", { src: url, alt: "", draggable: "false" }) : el("span", { class: "loading" }));
    const tile = el("div", { class: "pb-tile", role: this.mode === "select" ? "option" : "listitem", tabindex: "-1", "data-page": page, "data-key": ++this.seq },
      thumb,
      el("span", { class: "pb-pos" }),
      el("span", { class: "pb-check", "aria-hidden": "true" }, icon(this.effect === "remove" ? "x" : this.effect === "rotate" ? "cw" : "check")),
      el("div", { class: "pb-label" }, `Page ${page + 1}`),
      el("div", { class: "pb-actions" },
        this.#action("left", "Move earlier", (t) => this.#moveBy(t, -1)),
        this.#action("right", "Move later", (t) => this.#moveBy(t, 1)),
        this.#action("copy", "Duplicate", (t) => this.#duplicate(t)),
        this.#action("x", "Remove", (t) => this.#remove(t))));
    return tile;
  }

  #action(name, label, fn) {
    return el("button", { type: "button", class: "chip-btn", title: label, "aria-label": label, tabindex: "-1",
      onclick: (e) => fn(e.currentTarget.closest(".pb-tile")) }, icon(name));
  }

  #want(tile) {
    const page = Number(tile.dataset.page);
    if (this.thumbs.has(page)) return this.#fill(tile, this.thumbs.get(page));
    if (!this.queue.includes(tile)) this.queue.push(tile);
    this.#pump();
  }

  #fill(tile, url) {
    const thumb = tile.querySelector(".pb-thumb");
    if (!thumb.querySelector("img")) thumb.replaceChildren(el("img", { src: url, alt: "", draggable: "false" }));
  }

  #pump() {
    while (this.active < RENDERERS && this.queue.length) {
      const tile = this.queue.shift();
      if (!tile.dataset.visible || !tile.isConnected) continue;  // scrolled away; it is queued again when it comes back
      const page = Number(tile.dataset.page);
      this.active++;
      this.#render(page)
        .then((url) => { if (url) for (const t of this.grid.querySelectorAll(`[data-page="${page}"]`)) this.#fill(t, url); })
        .catch(() => {})
        .finally(() => { this.active--; this.#pump(); });
    }
  }

  #render(page) {
    if (this.rendering.has(page)) return this.rendering.get(page);
    const gen = this.gen, doc = this.doc;
    const job = (async () => {
      const pg = await doc.getPage(page + 1);
      const base = pg.getViewport({ scale: 1 });
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const viewport = pg.getViewport({ scale: (THUMB * ratio) / Math.max(base.width, base.height) });
      const canvas = document.createElement("canvas");
      canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
      await pg.render({ canvas, viewport }).promise;
      pg.cleanup();
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
      if (gen !== this.gen || !blob) return null;
      const url = URL.createObjectURL(blob);
      this.thumbs.set(page, url);
      return url;
    })();
    this.rendering.set(page, job);
    return job;
  }

  // ---- changing the order ------------------------------------------------------

  #change(order) {
    const before = this.order;
    if (order.join() === before.join()) return;
    this.history.push(before);
    this.#animate(() => this.#build(order));
    this.#update();
    this.onChange(this.order);
  }

  /** Report a change made directly in the DOM (drag, move, duplicate, remove). */
  #changed(before) {
    if (before.join() === this.order.join()) return;
    this.history.push(before);
    this.#update();
    this.onChange(this.order);
  }

  #moveBy(tile, delta) {
    const tiles = [...this.grid.children];
    const to = tiles.indexOf(tile) + delta;
    if (to < 0 || to >= tiles.length) return;
    const before = this.order;
    this.#animate(() => this.grid.insertBefore(tile, delta < 0 ? tiles[to] : tiles[to].nextSibling));
    this.#changed(before);
    tile.focus();
    this.#say(`Page ${Number(tile.dataset.page) + 1} moved to position ${to + 1} of ${tiles.length}.`);
  }

  #duplicate(tile) {
    const before = this.order;
    const copy = this.#tile(Number(tile.dataset.page));
    this.#animate(() => tile.after(copy));
    this.observer.observe(copy);
    this.#changed(before);
    copy.focus();
    this.#say(`Page ${Number(tile.dataset.page) + 1} duplicated.`);
  }

  #remove(tile) {
    const before = this.order;
    const next = tile.nextElementSibling || tile.previousElementSibling;
    this.#animate(() => tile.remove());
    this.#changed(before);
    if (next) next.focus(); else this.grid.focus();
    this.#say(`Page ${Number(tile.dataset.page) + 1} removed. Undo with Ctrl+Z.`);
  }

  /** Slide tiles from where they were to where they end up after mutate(). */
  #animate(mutate) {
    const keep = new Map([...this.grid.children].map((t) => [t.dataset.key, t.getBoundingClientRect()]));
    mutate();
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const moves = [];
    for (const t of this.grid.children) {
      const was = keep.get(t.dataset.key);
      if (!was || (this.drag && t === this.drag.tile)) continue;
      t.style.transition = "none"; t.style.transform = "";
      moves.push([t, was]);
    }
    const shifts = moves.map(([t, was]) => { const now = t.getBoundingClientRect(); return [t, was.left - now.left, was.top - now.top]; });
    for (const [t, dx, dy] of shifts) if (dx || dy) t.style.transform = `translate(${dx}px, ${dy}px)`;
    this.grid.offsetWidth;  // apply the start positions before animating
    for (const [t, dx, dy] of shifts) {
      if (!dx && !dy) { t.style.transition = ""; continue; }
      t.style.transition = "transform 180ms cubic-bezier(.2,.7,.3,1)";
      t.style.transform = "";
    }
  }

  // ---- dragging ------------------------------------------------------------------

  #pointerDown(e) {
    const tile = e.target.closest(".pb-tile");
    if (!tile || e.button !== 0 || e.target.closest("button") || this.drag) return;
    if (this.mode !== "order") {
      if (e.shiftKey) e.preventDefault();  // no text selection on Shift+click
      return;
    }
    e.preventDefault();  // no text selection; focus is handled below
    tile.focus({ preventScroll: true });
    const move = (ev) => this.#pointerMove(ev), up = (ev) => this.#endDrag(ev.type === "pointerup");
    this.drag = { tile, id: e.pointerId, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, started: false, move, up };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  #pointerMove(e) {
    const d = this.drag;
    if (!d || e.pointerId !== d.id) return;
    d.x = e.clientX; d.y = e.clientY;
    if (!d.started) {
      if (Math.hypot(d.x - d.x0, d.y - d.y0) < 5) return;
      this.#startDrag();
    }
    d.ghost.style.transform = `translate(${d.x - d.dx}px, ${d.y - d.dy}px) rotate(2deg)`;
    this.#placeAt(d.x, d.y);
  }

  #startDrag() {
    const d = this.drag, r = d.tile.getBoundingClientRect();
    d.started = true;
    d.before = this.order;
    d.dx = d.x0 - r.left; d.dy = d.y0 - r.top;
    d.ghost = d.tile.cloneNode(true);
    d.ghost.className = "pb-tile pb-ghost";
    d.ghost.removeAttribute("tabindex");
    d.ghost.style.width = r.width + "px";
    document.body.append(d.ghost);
    d.tile.classList.add("pb-placeholder");
    document.body.classList.add("pb-dragging");
    const scroll = () => {
      if (!this.drag || !this.drag.started) return;
      this.#autoScroll(this.drag.x, this.drag.y);
      d.raf = requestAnimationFrame(scroll);
    };
    d.raf = requestAnimationFrame(scroll);
  }

  /** Move the dragged tile to the slot under the pointer. Positions come
   *  from the layout (offsetLeft/Top), not getBoundingClientRect, so tiles
   *  that are still sliding into place don't make the target jitter. */
  #placeAt(x, y) {
    const d = this.drag, grid = this.grid, gr = grid.getBoundingClientRect();
    const px = x - gr.left - grid.clientLeft + grid.scrollLeft, py = y - gr.top - grid.clientTop + grid.scrollTop;
    let target = null, after = false, last = null;
    for (const t of grid.children) {
      if (t === d.tile) continue;
      last = t;
      if (px >= t.offsetLeft && px < t.offsetLeft + t.offsetWidth && py >= t.offsetTop && py < t.offsetTop + t.offsetHeight) {
        target = t; after = px > t.offsetLeft + t.offsetWidth / 2;
        break;
      }
    }
    if (!target && last && py >= last.offsetTop && (py > last.offsetTop + last.offsetHeight || px > last.offsetLeft + last.offsetWidth)) {
      target = last; after = true;  // past the last page
    }
    if (!target) return;
    const ref = after ? target.nextElementSibling : target;
    if (ref === d.tile || ref === d.tile.nextElementSibling) return;
    this.#animate(() => grid.insertBefore(d.tile, ref));
  }

  #autoScroll(x, y) {
    const edge = 48, speed = (dist) => Math.ceil(((edge - dist) / edge) * 14);
    const gr = this.grid.getBoundingClientRect();
    let moved = false;
    if (x >= gr.left && x <= gr.right) {
      const top = Math.max(gr.top, 0), bottom = Math.min(gr.bottom, innerHeight);
      if (y < top + edge && this.grid.scrollTop > 0) { this.grid.scrollTop -= speed(Math.max(y - top, 0)); moved = true; }
      else if (y > bottom - edge && this.grid.scrollTop + this.grid.clientHeight < this.grid.scrollHeight) { this.grid.scrollTop += speed(Math.max(bottom - y, 0)); moved = true; }
    }
    if (!moved) {  // the window itself, when the board is partly off screen
      if (y < edge && gr.top < 0) { scrollBy(0, -speed(Math.max(y, 0))); moved = true; }
      else if (y > innerHeight - edge && gr.bottom > innerHeight) { scrollBy(0, speed(Math.max(innerHeight - y, 0))); moved = true; }
    }
    if (moved) this.#placeAt(x, y);
  }

  #endDrag(commit) {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    window.removeEventListener("pointermove", d.move);
    window.removeEventListener("pointerup", d.up);
    window.removeEventListener("pointercancel", d.up);
    if (!d.started) return;
    cancelAnimationFrame(d.raf);
    d.ghost.remove();
    d.tile.classList.remove("pb-placeholder");
    document.body.classList.remove("pb-dragging");
    if (!commit) { if (this.doc && d.tile.isConnected) { this.#build(d.before); this.#update(); } return; }
    this.#changed(d.before);
    const pos = [...this.grid.children].indexOf(d.tile);
    this.#say(`Page ${Number(d.tile.dataset.page) + 1} moved to position ${pos + 1}.`);
  }

  // ---- keyboard ------------------------------------------------------------------

  #keyDown(e) {
    const tile = e.target.closest(".pb-tile");
    if (this.mode === "order" && (e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z") { e.preventDefault(); this.undo(); return; }
    if (this.mode === "select" && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); this.#pick(this.#all()); return; }
    if (!tile) return;
    if (this.mode === "select" && (e.key === " " || e.key === "Enter")) { e.preventDefault(); this.#toggle(tile, e.shiftKey); return; }
    if (this.mode === "split" && (e.key === " " || e.key === "Enter")) { e.preventDefault(); this.#toggleSplit(tile); return; }
    const tiles = [...this.grid.children], i = tiles.indexOf(tile);
    const cols = Math.max(1, tiles.filter((t) => t.offsetTop === tiles[0].offsetTop).length);
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols }[e.key];
    if (this.mode !== "order" && !step && e.key !== "Home" && e.key !== "End") return;
    if (step && (e.altKey || e.shiftKey) && this.mode === "order") {
      e.preventDefault();
      const to = Math.max(0, Math.min(tiles.length - 1, i + step));
      if (to !== i) this.#moveBy(tile, to - i);
    } else if (step) {
      if (this.mode !== "order" && (e.altKey || e.shiftKey)) return;
      e.preventDefault();
      const t = tiles[Math.max(0, Math.min(tiles.length - 1, i + step))];
      t.focus(); t.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const t = e.key === "Home" ? tiles[0] : tiles[tiles.length - 1];
      t.focus(); t.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault(); this.#remove(tile);
    } else if (e.key.toLowerCase() === "d" && !e.ctrlKey && !e.metaKey) {
      e.preventDefault(); this.#duplicate(tile);
    }
  }

  // ---- selecting (select mode) -----------------------------------------------------

  #all() { return [...Array(this.total).keys()]; }

  #click(e) {
    const tile = e.target.closest(".pb-tile");
    if (!tile) return;
    if (this.mode === "select") this.#toggle(tile, e.shiftKey);
    else if (this.mode === "split") this.#toggleSplit(tile);
  }

  #toggleSplit(tile) {
    const page = Number(tile.dataset.page);
    if (page === 0) { this.#say("The first page always starts the first file."); return; }
    const next = new Set(this.starts);
    next.has(page) ? next.delete(page) : next.add(page);
    this.#split([...next]);
    tile.focus({ preventScroll: true });
    this.#say(`${this.starts.has(page) ? "A new file starts" : "No new file starts"} at page ${page + 1}. ${this.status.textContent}.`);
  }

  /** Click: toggle one page. Shift+click: give every page from the last
   *  clicked one to this one the state the last clicked one has. */
  #toggle(tile, range) {
    const page = Number(tile.dataset.page);
    const next = new Set(this.picked);
    if (range && this.anchor !== null) {
      const on = next.has(this.anchor);
      const [a, b] = this.anchor < page ? [this.anchor, page] : [page, this.anchor];
      for (let p = a; p <= b; p++) on ? next.add(p) : next.delete(p);
    } else {
      next.has(page) ? next.delete(page) : next.add(page);
      this.anchor = page;
    }
    this.#pick([...next]);
    tile.focus({ preventScroll: true });
    this.#say(`Page ${page + 1} ${this.picked.has(page) ? "selected" : "not selected"}. ${this.status.textContent}.`);
  }

  #pick(pages) {
    const before = this.selection.join();
    this.picked = new Set(pages);
    this.#update();
    if (this.selection.join() !== before) this.onChange(this.selection);
  }

  #rove(tile) {
    for (const t of this.grid.querySelectorAll(".pb-tile[tabindex='0']")) if (t !== tile) t.tabIndex = -1;
    tile.tabIndex = 0;  // one tab stop for the whole grid
  }

  // ---- status ----------------------------------------------------------------------

  #update() {
    const tiles = [...this.grid.children];
    if (tiles.length && !tiles.some((t) => t.tabIndex === 0)) tiles[0].tabIndex = 0;
    if (this.mode === "select") return this.#updateSelect(tiles);
    if (this.mode === "split") return this.#updateSplit(tiles);
    tiles.forEach((t, i) => {
      t.querySelector(".pb-pos").textContent = i + 1;
      const page = Number(t.dataset.page);
      t.classList.toggle("moved", page !== i);
      t.setAttribute("aria-label", `Page ${page + 1}, position ${i + 1} of ${tiles.length}`);
    });
    if (this.mode === "view") {
      this.status.textContent = `${tiles.length} ${tiles.length === 1 ? "page" : "pages"} in the order they will have`;
      this.message.hidden = true;
      return;
    }
    const order = this.order, used = new Set(order);
    const removed = this.total - used.size, extra = order.length - used.size;
    const bits = [`${order.length} ${order.length === 1 ? "page" : "pages"}`];
    if (removed) bits.push(`${removed} removed`);
    if (extra) bits.push(`${extra} duplicated`);
    if (order.join() === [...Array(this.total).keys()].join()) bits.push("original order");
    this.status.textContent = bits.join(" · ");
    this.undoBtn.disabled = !this.history.length;
    this.message.hidden = tiles.length > 0;
    if (!tiles.length) this.message.textContent = "All pages removed. Use Undo or Reset to bring them back.";
  }

  #updateSplit(tiles) {
    let file = 0;
    const ranges = [];
    tiles.forEach((t) => {
      const page = Number(t.dataset.page);
      if (page === 0 || this.starts.has(page)) { file++; ranges.push([page, page]); }
      ranges[ranges.length - 1][1] = page;
      t.querySelector(".pb-pos").textContent = page + 1;
      t.classList.toggle("split-start", page > 0 && this.starts.has(page));
      t.dataset.file = file;
      t.style.setProperty("--file-tint", file % 2 ? "transparent" : "var(--surface-2)");
      t.setAttribute("aria-label", `Page ${page + 1}, file ${file}${this.starts.has(page) ? ", starts a new file" : ""}`);
      const label = t.querySelector(".pb-file") || t.appendChild(el("span", { class: "pb-file" }));
      label.textContent = page === 0 || this.starts.has(page) ? `File ${file}` : "";
    });
    const span = ([a, b]) => (a === b ? `${a + 1}` : `${a + 1}–${b + 1}`);
    this.status.textContent = ranges.length === 1 ? "1 file — click a page to start a new file there"
      : `${ranges.length} files: pages ${ranges.slice(0, 6).map(span).join(", ")}${ranges.length > 6 ? ", …" : ""}`;
    this.message.hidden = true;
  }

  #updateSelect(tiles) {
    const n = this.picked.size, total = this.total;
    // Rotate with nothing picked rotates every page (an empty page list means all).
    const all = this.effect === "rotate" && n === 0;
    tiles.forEach((t) => {
      const page = Number(t.dataset.page), on = this.picked.has(page);
      t.querySelector(".pb-pos").textContent = page + 1;
      t.classList.toggle("picked", on);
      t.classList.toggle("turned", this.effect === "rotate" && (on || all) && this.rotation !== 0);
      t.setAttribute("aria-selected", String(on));
      t.setAttribute("aria-label", `Page ${page + 1}`);
    });
    this.root.classList.toggle("has-picked", n > 0);
    const pages = (k) => `${k} ${k === 1 ? "page" : "pages"}`;
    this.status.textContent = {
      keep: n ? `${pages(n)} of ${total} selected to extract` : `Click the pages to extract`,
      remove: n === total ? `All pages selected — at least one page has to stay`
        : n ? `${pages(n)} will be deleted · ${pages(total - n)} left` : `Click the pages to delete`,
      rotate: n ? `${pages(n)} of ${total} will be rotated` : `No pages selected — all ${total} will be rotated`,
      insert: n === total ? `A blank page after every page` : n ? `Blank pages after ${pages(n)}` : `Click the pages to put a blank page after`,
    }[this.effect] || `${pages(n)} selected`;
    this.root.classList.toggle("pb-warn", this.effect === "remove" && n === total);
    this.message.hidden = true;
  }

  #state(name, detail) {
    this.root.dataset.state = name;
    this.toolbar.hidden = name !== "ready";
    this.grid.hidden = name !== "ready";
    const text = {
      empty: "",
      loading: "Loading pages…",
      password: "This PDF is password-protected. Enter its password under Options to see the pages.",
      "wrong-password": "That password doesn't open this PDF. Check the password under Options.",
      error: `The pages can't be shown${detail ? ": " + detail : "."}`,
    }[name];
    if (name === "ready") this.#update();
    else { this.message.hidden = !text; this.message.textContent = text || ""; }
  }

  #say(text) { this.live.textContent = text; }
}
