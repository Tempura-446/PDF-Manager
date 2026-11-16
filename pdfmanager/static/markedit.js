/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Changing a markup after it's placed (markup.py: "change"). With Shapes,
// Stamps or Notes on in the annotate bar, pressing on a markup made with
// ZPDF's own tools selects it: handles show on its corners, ends or points.
// Drag a handle to reshape it, the markup itself to move it; Delete removes
// it, the arrow keys nudge it, Esc lets go; the colours and the thickness
// in the bar change it; a double-click changes a callout's words. The
// server draws it again with the tool that drew it. A part of Reader.

import { el } from "./readerkit.js";

const EDIT_MODES = new Set(["SHAPE", "STAMPS", "NOTE"]);
const BOX_KINDS = new Set(["rect", "ellipse", "cloud", "stamp"]);      // changed by their rectangle's corners
const POINT_KINDS = new Set(["line", "arrow", "dimension", "polyline", "polycloud"]);
const NEAR = 7;       // px: how close a press must be to a markup's outline
const GRIP = 9;       // px: how close to a handle
const CLOUD_BUMP = 6; // PDF units: how far a cloud's bumps stand out from its outline
const TWICE = 500;    // ms: two presses this close together are a double-click

const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
/** How far ``p`` is from the segment a-b. */
function fromSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], len = dx * dx + dy * dy;
  const t = len ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len)) : 0;
  return dist(p, [a[0] + t * dx, a[1] + t * dy]);
}
const corners = ([x0, y0, x1, y1]) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];

/** The points a markup is changed by: [{ key, pt }] in PDF coordinates. */
export function handlesOf(shape) {
  if (BOX_KINDS.has(shape.kind)) return corners(shape.rect).map((pt, i) => ({ key: "corner" + i, pt }));
  if (POINT_KINDS.has(shape.kind)) return shape.points.map((pt, i) => ({ key: "point" + i, pt }));
  if (shape.kind === "callout") return [{ key: "tip", pt: shape.tip }, { key: "at", pt: shape.at }];
  return [];  // a note: moved whole
}

/** How far a point is from a markup's own lines (PDF units); 0 inside a
 *  stamp, a note or a callout's box. */
export function distanceTo(shape, p) {
  const inside = ([x0, y0, x1, y1]) => p[0] >= x0 && p[0] <= x1 && p[1] >= y0 && p[1] <= y1;
  const along = (pts, closed) => {
    let best = Infinity;
    for (let i = 0; i + 1 < pts.length; i++) best = Math.min(best, fromSegment(p, pts[i], pts[i + 1]));
    if (closed && pts.length > 2) best = Math.min(best, fromSegment(p, pts[pts.length - 1], pts[0]));
    return best;
  };
  if (shape.kind === "stamp" || shape.kind === "note") return inside(shape.bounds) ? 0 : Infinity;
  if (shape.kind === "callout") return Math.min(fromSegment(p, shape.tip, shape.at), inside(shape.bounds) && dist(p, shape.at) < dist(p, shape.tip) ? 0 : Infinity);
  if (shape.kind === "ellipse") {
    const [x0, y0, x1, y1] = shape.rect, rx = (x1 - x0) / 2, ry = (y1 - y0) / 2;
    if (rx <= 0 || ry <= 0) return Infinity;
    const r = Math.hypot((p[0] - (x0 + x1) / 2) / rx, (p[1] - (y0 + y1) / 2) / ry);
    return Math.abs(r - 1) * Math.min(rx, ry);
  }
  if (shape.kind === "rect") return along(corners(shape.rect), true);
  // A cloud's bumps stand out from its outline: a press on them counts too.
  if (shape.kind === "cloud") return Math.max(0, along(corners(shape.rect), true) - CLOUD_BUMP);
  if (shape.kind === "polycloud") return Math.max(0, along(shape.points, true) - CLOUD_BUMP);
  return along(shape.points, false);
}

/** ``shape`` with the handle ``key`` put at ``pt`` (or, with no key, moved by ``by``). */
export function reshaped(shape, key, pt, by = null) {
  const out = { ...shape };
  if (!key) {
    const [dx, dy] = by;
    const move = ([x, y]) => [x + dx, y + dy];
    if (out.points) out.points = out.points.map(move);
    if (out.rect) out.rect = [out.rect[0] + dx, out.rect[1] + dy, out.rect[2] + dx, out.rect[3] + dy];
    for (const k of ["tip", "at"]) if (out[k]) out[k] = move(out[k]);
    if (out.bounds) out.bounds = [out.bounds[0] + dx, out.bounds[1] + dy, out.bounds[2] + dx, out.bounds[3] + dy];
    return out;
  }
  if (key.startsWith("corner")) {
    const i = +key.slice(6), fixed = corners(shape.rect)[(i + 2) % 4];   // the opposite corner stays
    out.rect = [Math.min(fixed[0], pt[0]), Math.min(fixed[1], pt[1]), Math.max(fixed[0], pt[0]), Math.max(fixed[1], pt[1])];
  } else if (key.startsWith("point")) {
    out.points = shape.points.map((q, i) => (i === +key.slice(5) ? [pt[0], pt[1]] : q));
  } else out[key] = [pt[0], pt[1]];
  return out;
}

/** What to send for ``after`` instead of ``before`` (see markup.py "change"). */
export function changeFor(before, after, key, by) {
  if (!key) return { move: by };
  if (key.startsWith("corner")) return { rect: after.rect };
  if (key.startsWith("point")) return { points: after.points };
  return { [key]: after[key] };
}

export const MarkupEditing = (Base) => class extends Base {
  /** The changeable markups of the open document (asked of the server once
   *  for each version of it; null while that's on its way). */
  _markShapes() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return null;
    const version = tab.version || 0;
    if (this.markCache?.tab === tab && this.markCache.version === version) return this.markCache.pages;
    if (this.markAsking?.tab === tab && this.markAsking.version === version) return null;
    const asking = this.markAsking = { tab, version };
    (async () => {
      try {
        const res = await this._post("/api/markup/describe", await this._currentBytes(), tab);
        if (!res.ok) return;
        const { pages } = await res.json();
        if (this.activeTab !== tab || (tab.version || 0) !== version) return;
        this.markCache = { tab, version, pages };
        if (this.markAfter) { const again = this.markAfter; this.markAfter = null; again(pages); }
        else this.#refreshSelection(pages);
      } catch (e) { /* asked again at the next press */ } finally {
        if (this.markAsking === asking) this.markAsking = null;
      }
    })();
    return null;
  }

  /** The document was written again some other way (Undo, another tool)
   *  with a markup selected: it is picked up as it is now, or let go if it
   *  isn't there any more. */
  #refreshSelection(pages) {
    const sel = this.markSel;
    if (!sel || this.markDrag) return;
    const now = pages?.[sel.page]?.[sel.index];
    if (now && now.kind === sel.shape.kind) { sel.shape = now; this._markDraw(sel.page); } else this._markLetGo();
  }

  /** The annotate bar's tool changed, or the document did: have the markups ready. */
  _markReady() {
    if (!EDIT_MODES.has(this.markupMode) || (this.markSel && this.markSel.tab !== this.activeTab)) this._markLetGo();
    if (EDIT_MODES.has(this.markupMode)) this._markShapes();
  }

  #markAt(n, pt, scale) {
    const shapes = this._markShapes()?.[n] || [];
    let best = null;
    for (let index = shapes.length - 1; index >= 0; index--) {   // the one drawn last is on top
      const d = distanceTo(shapes[index], pt) * scale;
      if (d <= NEAR && (!best || d < best.d)) best = { index, d };
    }
    return best ? { index: best.index, shape: shapes[best.index] } : null;
  }

  /** A press on the page with a markup tool on: true when it selected a
   *  markup or began changing the selected one (so no new one is begun). */
  _markEditDown(e, at) {
    if (!EDIT_MODES.has(this.markupMode) || !at) return false;
    const scale = at.view.viewport.scale;
    const sel = this.markSel;
    // While a change is being written (or the markups asked for again), a press waits its turn.
    if (this.markBusy || (sel && !this._markShapes())) return true;
    // A second press on the selected callout soon after the first: its words.
    // (Counted here: a pointerdown doesn't say it's the second of two.)
    const last = this.markPress;
    this.markPress = { t: e.timeStamp, x: e.clientX, y: e.clientY };
    if (sel && sel.page === at.n && sel.shape.kind === "callout" && last && e.timeStamp - last.t < TWICE
        && Math.hypot(e.clientX - last.x, e.clientY - last.y) < 6 && distanceTo(sel.shape, at.pt) * scale <= NEAR) {
      this.markPress = null;
      this._markEditText();
      return true;
    }
    if (sel && sel.page === at.n) {
      const grip = handlesOf(sel.shape).find((h) => dist(h.pt, at.pt) * scale <= GRIP);
      if (grip) { this.#beginDrag(e, at, grip.key); return true; }
    }
    const hit = this.#markAt(at.n, at.pt, scale);
    if (!hit) { if (sel) this._markLetGo(); return false; }
    this.#select(at.n, hit.index);
    this.#beginDrag(e, at, null);
    return true;
  }

  #select(page, index) {
    const shape = this._markShapes()?.[page]?.[index];
    if (!shape) { this._markLetGo(); return; }
    this.markSel = { page, index, shape, tab: this.activeTab };
    if (this.deleteBtn) this.deleteBtn.disabled = false;   // the bar's bin removes it too
    this._markDraw(page);
    this._hint?.(shape.kind === "note" ? "Drag the note to move it. Delete removes it; Esc lets go."
      : "Drag a handle to reshape it, or the markup to move it. Delete removes it; Esc lets go.", 5000);
  }

  _markLetGo() {
    const sel = this.markSel;
    this.markSel = this.markDrag = null;
    document.querySelector(".rd-markedit")?.remove();
    if (sel) {
      this._endHint?.();
      if (this.deleteBtn) this.deleteBtn.disabled = true;
    }
  }

  /** Remove the selected markup (the Delete key, the bar's bin): true when there was one. */
  _markDelete() {
    const sel = this.markSel;
    if (!sel || !EDIT_MODES.has(this.markupMode)) return false;
    if (this.markBusy || this.markPending) return true;
    const { page } = sel, id = sel.shape.id;
    this._markLetGo();
    this._applyMarkup([{ op: "delete", page, id }], "Could not remove that: ");
    return true;
  }

  #beginDrag(e, at, key) {
    this.markDrag = { key, start: at.pt, pointer: e.pointerId, page: at.n, view: at.view, shape: this.markSel.shape, moved: false };
    this.container.setPointerCapture?.(e.pointerId);
  }

  _markEditMove(e) {
    const d = this.markDrag;
    if (!d || e.pointerId !== d.pointer) return false;
    const box = d.view.div.getBoundingClientRect();
    const x = e.clientX - box.left - d.view.div.clientLeft, y = e.clientY - box.top - d.view.div.clientTop;
    let pt = d.view.viewport.convertToPdfPoint(x, y);
    if (!d.moved && dist(pt, d.start) * d.view.viewport.scale < 3) return true;   // not a drag yet
    d.moved = true;
    // A dimension's ends go onto the drawing's lines, as when it was drawn (Alt: exactly here).
    if (d.key && d.shape.kind === "dimension" && !e.altKey) pt = this._snapNear(d.page, pt, d.view.viewport.scale);
    d.by = [pt[0] - d.start[0], pt[1] - d.start[1]];
    d.now = reshaped(d.shape, d.key, pt, d.by);
    this._markDraw(d.page, d.now);
    return true;
  }

  _markEditUp(e) {
    const d = this.markDrag;
    if (!d || e.pointerId !== d.pointer) return false;
    this.markDrag = null;
    if (!d.moved || !d.now) return true;      // a click: selected, nothing changed
    const change = changeFor(d.shape, d.now, d.key, d.by);
    if (d.shape.kind === "dimension" && change.points) change.value = this._dimensionOf(d.page, change.points[0], change.points[1]).value;
    this._markChange(change);
    return true;
  }

  /** Send a change to the selected markup; it stays selected afterwards. */
  async _markChange(change, failure = "Could not change that: ") {
    const sel = this.markSel;
    if (!sel || this.markBusy) return;      // (one change at a time: a held arrow key doesn't pile them up)
    const { page, index } = sel, tab = this.activeTab, version = tab?.version || 0;
    // From here until the markup is picked up again its keys are still its own
    // (markPending): the selection is gone for a moment while the page is redrawn.
    this.markBusy = this.markPending = true;
    try {
      await this._applyMarkup([{ op: "change", page, id: sel.shape.id, ...change }], failure);
    } finally { this.markBusy = false; }
    if (this.activeTab !== tab) { this.#settled(false); return; }
    if ((tab.version || 0) === version) { this._markDraw(page); this.#settled(false); return; }   // it didn't work (and said so)
    // The document was written again: its markups are asked for anew, and this one picked up again.
    this.markAfter = () => {
      const on = EDIT_MODES.has(this.markupMode) && this.activeTab === tab;
      if (on) this.#select(page, index);
      this.#settled(on);
    };
    if (this._markShapes()) { const again = this.markAfter; this.markAfter = null; again(); }
  }

  /** A change is over and the markup picked up again (or not: ``on``). The
   *  arrow keys pressed meanwhile are sent as one move, so a held key ends
   *  where its presses say. */
  #settled(on) {
    this.markPending = false;
    const by = this.markQueued;
    this.markQueued = null;
    if (on && by && (by[0] || by[1]) && this.markSel) this._markChange({ move: by });
  }

  /** Whether a key may be the selected markup's (also just after a change,
   *  while it is being picked up again). */
  _markHasKeys() {
    if (this.markPending && !this.markBusy && !this.markAfter && !this.markAsking) this.#settled(false);   // (it never came back)
    return !!this.markSel || !!this.markPending;
  }

  /** Delete, Esc and the arrow keys for the selected markup: true when the key was its. */
  _markKey(e) {
    const sel = this.markSel;
    // (While the page is shown again after a change the tool reads as off for a moment: markPending says it's still ours.)
    if (!this.markPending && (!sel || !EDIT_MODES.has(this.markupMode))) return false;
    const step = e.shiftKey ? 10 : 1;
    const by = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
    if (e.key === "Escape") { this.markQueued = null; this._markLetGo(); return true; }
    if (this.markBusy || this.markPending || !this._markShapes()) {   // still writing the last change
      if (by) this.markQueued = [(this.markQueued?.[0] || 0) + by[0], (this.markQueued?.[1] || 0) + by[1]];
      return true;                                                   // (and no key scrolls the page meanwhile)
    }
    if (e.key === "Delete" || e.key === "Backspace") return this._markDelete();
    if (by) { this._markChange({ move: by }); return true; }
    if (e.key === "Enter" && sel.shape.kind === "callout") { this._markEditText(); return true; }
    return false;
  }

  /** The bar's colour or thickness was chosen with a markup selected: it takes it. */
  _markRestyle(style) {
    const sel = this.markSel;
    if (!sel || !EDIT_MODES.has(this.markupMode) || sel.shape.kind === "stamp" || sel.shape.kind === "note") return;
    // (Only what's different: choosing the colour it has already writes nothing.)
    const same = (key) => String(style[key]).toLowerCase() === String(sel.shape[key]).toLowerCase();
    if (Object.keys(style).every(same)) return;
    this._markChange(style);
  }

  /** Change a callout's words. */
  async _markEditText() {
    const sel = this.markSel;
    if (!sel || sel.shape.kind !== "callout") return;
    const area = el("textarea", { rows: 3, class: "rd-calloutwords", "aria-label": "The callout's words" });
    area.value = sel.shape.text || "";
    const answer = await this._dialog("Callout", [area], [["cancel", "Cancel"], ["ok", "Change", true]],
      { ready: () => !!area.value.trim() });
    const words = area.value.trim();
    if (answer !== "ok" || !words || words === sel.shape.text) return;
    this._markChange({ text: words });
  }

  /** The selected markup's outline and handles over its page (``shape``:
   *  as it would be, while it's being dragged). */
  _markDraw(n, shape = null) {
    const sel = this.markSel;
    if (!sel) { document.querySelector(".rd-markedit")?.remove(); return; }
    if (sel.page !== n) return;             // (another page was shown again: the selected one's handles stay)
    document.querySelector(".rd-markedit")?.remove();
    const view = this.pdfViewer.getPageView(n - 1);
    if (!view?.div || !view.viewport) return;
    const s = shape || sel.shape, vp = view.viewport;
    const px = (p) => vp.convertToViewportPoint(p[0], p[1]);
    const path = (pts, closed) => pts.map((p, i) => `${i ? "L" : "M"}${px(p).map((v) => v.toFixed(1)).join(" ")}`).join(" ") + (closed ? " Z" : "");
    let outline;
    if (s.kind === "ellipse") {
      const [a, b] = [px([s.rect[0], s.rect[1]]), px([s.rect[2], s.rect[3]])];
      outline = `<ellipse cx="${(a[0] + b[0]) / 2}" cy="${(a[1] + b[1]) / 2}" rx="${Math.abs(b[0] - a[0]) / 2}" ry="${Math.abs(b[1] - a[1]) / 2}"/>`;
    } else if (BOX_KINDS.has(s.kind)) outline = `<path d="${path(corners(s.rect), true)}"/>`;
    else if (POINT_KINDS.has(s.kind)) outline = `<path d="${path(s.points, s.kind === "polycloud")}"/>`;
    else if (s.kind === "callout") outline = `<path d="${path([s.tip, s.at])}"/>`;
    else outline = `<path d="${path(corners(s.bounds), true)}"/>`;
    const grips = handlesOf(s).map((h) => {
      const [x, y] = px(h.pt);
      return `<circle class="rd-markgrip" data-key="${h.key}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5"/>`;
    }).join("");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "rd-markedit" + (shape ? " rd-markmoving" : ""));
    svg.dataset.kind = s.kind;
    svg.innerHTML = `<g class="rd-markline">${outline}</g>${grips}`;
    view.div.append(svg);
  }
};
