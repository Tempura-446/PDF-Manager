/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Changing a markup after it's placed (markup.py: "change"). With Shapes,
// Stamps or Notes on in the annotate bar, pressing on a markup made with
// ZPDF's own tools selects it: handles show on its corners, ends or points.
// Drag a handle to reshape it, the markup itself to move it; Delete removes
// it, the arrow keys nudge it, Esc lets go; the colours and the thickness
// in the bar change it; a double-click changes a callout's words. The
// server draws it again with the tool that drew it. A part of Reader.

import { distance as dist, rectangle } from "./measure.js";
import { el } from "./readerkit.js";

const EDIT_MODES = new Set(["SHAPE", "STAMPS", "NOTE"]);
const BOX_KINDS = new Set(["rect", "ellipse", "cloud", "stamp"]);      // changed by their rectangle's corners
const POINT_KINDS = new Set(["line", "arrow", "dimension", "polyline", "polycloud"]);
const NEAR = 7;       // px: how close a press must be to a markup's outline
const GRIP = 9;       // px: how close to a handle
const TWICE = 500;    // ms: two presses this close together are a double-click
const NUDGE_REST = 350;  // ms without an arrow key before the nudges made are written
const HELD_FOR = 1500;   // ms a key counts as still held after its last key-down (if its key-up never came)

/** How far ``p`` is from the segment a-b. */
function fromSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], len = dx * dx + dy * dy;
  const t = len ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len)) : 0;
  return dist(p, [a[0] + t * dx, a[1] + t * dy]);
}
const corners = ([x0, y0, x1, y1]) => rectangle([x0, y0], [x1, y1]);

/** The points a markup is changed by: [{ key, pt }] in PDF coordinates. */
function handlesOf(shape) {
  if (BOX_KINDS.has(shape.kind)) return corners(shape.rect).map((pt, i) => ({ key: "corner" + i, pt }));
  if (POINT_KINDS.has(shape.kind)) return shape.points.map((pt, i) => ({ key: "point" + i, pt }));
  if (shape.kind === "callout") return [{ key: "tip", pt: shape.tip }, { key: "at", pt: shape.at }];
  return [];  // a note: moved whole
}

/** How far a point is from a markup's own lines (PDF units); 0 inside a
 *  stamp, a note or a callout's box. */
function distanceTo(shape, p) {
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
  // (How far they reach grows with the line: markup.py draws them max(10, 6 x width) across.)
  const reach = Math.max(10, (+shape.width || 2) * 6) / 2 + (+shape.width || 2);
  if (shape.kind === "cloud") return Math.max(0, along(corners(shape.rect), true) - reach);
  if (shape.kind === "polycloud") return Math.max(0, along(shape.points, true) - reach);
  return along(shape.points, false);
}

/** ``shape`` with the handle ``key`` put at ``pt`` (or, with no key, moved by ``by``). */
function reshaped(shape, key, pt, by = null) {
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

/** What to send so the markup becomes ``after`` (see markup.py "change"). */
function changeFor(after, key, by) {
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
    // Kept with the tab (so it goes when the tab does, and is still there after a look at another tab).
    // ``pages`` null: it couldn't be listed; not asked again for the same version, which would send the whole file each time.
    if (tab.markShapes?.version === version) return tab.markShapes.pages;
    if (this.markAsking?.tab === tab && this.markAsking.version === version) return null;
    const asking = this.markAsking = { tab, version };
    (async () => {
      let pages = null, stale = false;
      try {
        const res = await this._post("/api/markup/describe", await this._currentBytes(), tab);
        if (res.ok) ({ pages } = await res.json());
        stale = this.activeTab === tab && (tab.version || 0) !== version;   // written again meanwhile
      } catch (e) { /* the list didn't come: handled below */ } finally {
        if (this.markAsking === asking) this.markAsking = null;
      }
      if (stale) { this._markShapes(); return; }         // ask for how it is now
      if ((tab.version || 0) === version) tab.markShapes = { version, pages };
      if (this.activeTab !== tab || !pages) {
        // Another document is shown, or the list didn't come: nothing is picked up again.
        this.markAfter = null;
        if (this.markPending) this.#settled(false);
        return;
      }
      if (!this.#runAfter()) this.#refreshSelection(pages);
    })();
    return null;
  }

  /** The answer to a change of markups says how they are now (``X-Pdfm-Markups``):
   *  kept for the document's new version, so the whole file isn't sent again to ask. */
  _markSeed(res, tab) {
    try {
      const said = res.headers.get("X-Pdfm-Markups");
      if (said) tab.markShapes = { version: tab.version || 0, pages: JSON.parse(said) };
    } catch (e) { /* asked for the usual way */ }
  }

  /** The document was written again some other way (Undo, another tool)
   *  with a markup selected: it is picked up as it is now, or let go if it
   *  isn't there any more. */
  #refreshSelection(pages) {
    const sel = this.markSel;
    if (!sel || this.markDrag) return;
    // Found by its name, which it keeps through every change (its place in the list may not be the same).
    const index = (pages?.[sel.page] || []).findIndex((s) => s.name === sel.shape.name);
    if (index >= 0) {
      Object.assign(sel, { index, shape: pages[sel.page][index], version: this.activeTab?.version || 0 });
      this._markDraw(sel.page);
    } else this._markLetGo();
  }

  /** What waits for the markups to be listed again is done now: true when something did. */
  #runAfter() {
    const again = this.markAfter;
    this.markAfter = null;
    again?.();
    return !!again;
  }

  /** The annotate bar's tool changed, or the document did: have the markups ready. */
  _markReady() {
    const on = EDIT_MODES.has(this.markupMode), sel = this.markSel, tab = this.activeTab;
    if (!on || (sel && sel.tab !== tab)) {
      this._markLetGo();
      // The tool goes off for a moment each time the document is shown again (Undo, Redo, another
      // change): what was selected in this document is remembered, to be picked up again by its name.
      if (sel && sel.tab === tab) this.markWas = { tab, page: sel.page, name: sel.shape.name, version: sel.version };
    }
    if (!on) return;
    const was = this.markWas;
    this.markWas = null;
    // Only when the document was written again since (not when the tool was just put away and taken up again).
    if (was && was.tab === tab && !this.markSel && (tab.version || 0) !== was.version) {
      const again = () => {
        const index = (this._markShapes()?.[was.page] || []).findIndex((shape) => shape.name === was.name);
        if (index >= 0 && EDIT_MODES.has(this.markupMode) && this.activeTab === tab && !this.markSel) this.#select(was.page, index);
      };
      if (this._markShapes()) again(); else this.markAfter ||= again;
      return;
    }
    this._markShapes();
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
    if (this.markNudge) {
      // Arrow presses not written yet are written first. A press on the markup waits its turn;
      // one beside it lets go, as it would have (the markup isn't picked up again after the write).
      const beside = sel && !(sel.page === at.n && (distanceTo(sel.shape, at.pt) * scale <= NEAR
        || handlesOf(sel.shape).some((h) => dist(h.pt, at.pt) * scale <= GRIP)));
      this._markFlush();
      if (beside && this.markPending) { this.markDropped = true; this._markLetGo(); }
      return true;
    }
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
      if (grip) { this.#focusPage(); this.#beginDrag(e, at, grip.key); return true; }
    }
    const hit = this.#markAt(at.n, at.pt, scale);
    this.markClick = null;
    if (!hit) { if (sel) this._markLetGo(); return false; }
    const selected = sel && sel.page === at.n && sel.index === hit.index;
    // With a shape that's dragged out, a press on a markup that isn't selected may be the
    // start of a new one (a dimension continued from another's end, an arrow from a cloud):
    // a click selects it, a drag draws. Moving it takes selecting it first.
    if (!selected && this.markupMode === "SHAPE" && this._shapeIsDragged?.()) {
      if (sel) this._markLetGo();
      this.markClick = { page: at.n, index: hit.index, x: e.clientX, y: e.clientY, pointer: e.pointerId };
      return false;
    }
    if (!selected) this.#select(at.n, hit.index);
    this.#focusPage();
    this.#beginDrag(e, at, null);
    return true;
  }

  #select(page, index) {
    const shape = this._markShapes()?.[page]?.[index];
    if (!shape) { this._markLetGo(); return; }
    this.markSel = { page, index, shape, tab: this.activeTab, version: this.activeTab?.version || 0 };
    if (this.deleteBtn) this.deleteBtn.disabled = false;   // the bar's bin removes it too

    this._markDraw(page);
    this._hint?.(shape.kind === "note" ? "Drag the note to move it. Delete removes it; Esc lets go."
      : "Drag a handle to reshape it, or the markup to move it. Delete removes it; Esc lets go.", 5000);
  }

  /** A press on the page took a markup: its keys go to the page from then on, not to
   *  whatever had the focus (a button of the bar, where Up and Down walk the buttons; a
   *  page in the list, where the arrows turn pages). */
  #focusPage() {
    if (document.activeElement === this.container || document.querySelector("dialog[open]")) return;
    this.container?.focus?.({ preventScroll: true });
  }

  _markLetGo() {
    const sel = this.markSel;
    this.markSel = this.markDrag = this.markWas = null;
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
    this.#takeNudge();                      // (moved a little, then removed: only the removing is written)
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
    const d = this.markDrag, c = this.markClick;
    if (!d && c && e.pointerId === c.pointer) {
      // The press on a markup ended where it began: it's selected (the shape begun there is too small to keep).
      this.markClick = null;
      if (Math.hypot(e.clientX - c.x, e.clientY - c.y) < 4 && EDIT_MODES.has(this.markupMode)) {
        this.#select(c.page, c.index);
        if (this.markSel) this.#focusPage();
      }
      return false;
    }
    if (!d || e.pointerId !== d.pointer) return false;
    this.markDrag = null;
    if (!d.moved || !d.now) return true;      // a click: selected, nothing changed
    const change = changeFor(d.now, d.key, d.by);
    if (d.shape.kind === "dimension" && change.points) {
      // Its words in the scale it was drawn with (the sheet's own, if it had none then, which it then takes).
      const { value, scale } = this._dimensionOf(d.page, change.points[0], change.points[1], d.shape.scale || undefined);
      change.value = value;
      if (!d.shape.scale && scale) change.scale = scale;
    }
    this._markChange(change);
    return true;
  }

  /** Send a change to the selected markup; it stays selected afterwards. */
  async _markChange(change, failure = "Could not change that: ") {
    const sel = this.markSel;
    if (!sel || this.markBusy) return;      // (one change at a time)
    // Arrow presses not written yet go with this change, as one.
    const waiting = this.#takeNudge();
    if (waiting && !change.move) change = { ...change, move: waiting };
    const { page, index } = sel, { name } = sel.shape, tab = this.activeTab, version = tab?.version || 0;
    this.markPage = page;
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
      const on = EDIT_MODES.has(this.markupMode) && this.activeTab === tab && !this.markDropped;   // (Esc meanwhile: let go)
      if (on) {
        const at = (this._markShapes()?.[page] || []).findIndex((s) => s.name === name);   // by its name; else where it was
        this.#select(page, at >= 0 ? at : index);
      }
      this.#settled(on);
    };
    if (this._markShapes()) this.#runAfter();
  }

  /** A change is over and the markup picked up again (or not: ``on``). The
   *  arrow keys pressed meanwhile count from here, so a held key ends where
   *  its presses say. */
  #settled(on) {
    this.markPending = this.markDropped = false;
    const by = this.markQueued;
    this.markQueued = null;
    if (on && by && this.markSel) this.#nudge(by);
  }

  /** The arrow keys move the markup like a drag does: its outline goes at
   *  once, press after press, and the file is written once the keys have
   *  rested for a moment. So a held key, or a run of presses, is one change
   *  and one step for Undo. */
  #nudge(by) {
    const sel = this.markSel, was = this.markNudge?.by || [0, 0];
    clearTimeout(this.markNudge?.timer);
    this.markNudge = { by: [was[0] + by[0], was[1] + by[1]], timer: setTimeout(() => this.#rested(), NUDGE_REST) };
    this.markHeld = performance.now();
    this._markDraw(sel.page, reshaped(sel.shape, null, null, this.markNudge.by));
  }

  /** The keys have rested: written, unless an arrow key is still held down
   *  (the pause before a held key starts repeating is longer than the rest). */
  #rested() {
    const nudge = this.markNudge;
    if (!nudge) return;
    if (this.markHeld && performance.now() - this.markHeld < HELD_FOR) nudge.timer = setTimeout(() => this.#rested(), NUDGE_REST);
    else this._markFlush();
  }

  /** An arrow key was let go: the rest before writing counts from its last press. */
  _markKeyUp(e) {
    if (e.key?.startsWith("Arrow")) this.markHeld = null;
  }

  /** The presses not written yet, taken (so they are written once): [dx, dy] or null. */
  #takeNudge() {
    const nudge = this.markNudge;
    this.markNudge = null;
    clearTimeout(nudge?.timer);
    return nudge && (nudge.by[0] || nudge.by[1]) ? nudge.by : null;
  }

  /** Write the arrow presses made so far, now (the keys rested; or something
   *  else is about to happen to the document: a save, another change). */
  _markFlush() {
    if (!this.markNudge) return;
    if (!this.markSel || this.markBusy) { this.#takeNudge(); if (this.markSel) this._markDraw(this.markSel.page); return; }
    this._markChange({ move: this.#takeNudge() || [0, 0] });
  }

  /** ``step`` points left, right, up or down as the page is seen (it may be
   *  turned), in the page's own directions. */
  #nudgeBy(key, step) {
    const seen = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[key];
    if (!seen) return null;
    const vp = this.pdfViewer?.getPageView((this.markSel?.page || this.markPage || 1) - 1)?.viewport;
    if (!vp) return [seen[0], -seen[1]];
    const a = vp.convertToPdfPoint(0, 0), b = vp.convertToPdfPoint(seen[0] * vp.scale, seen[1] * vp.scale);
    return [Math.round((b[0] - a[0]) * 1e4) / 1e4, Math.round((b[1] - a[1]) * 1e4) / 1e4];
  }

  /** Whether a key may be the selected markup's (also just after a change,
   *  while it is being picked up again). */
  _markHasKeys() {
    if (this.markPending && !this.markBusy && !this.markAsking) { this.markAfter = null; this.#settled(false); }   // (it never came back)
    return !!this.markSel || !!this.markPending || !!this.markHush;
  }

  /** Delete, Esc and the arrow keys for the selected markup: true when the key was its. */
  _markKey(e) {
    const sel = this.markSel;
    // Esc was pressed with an arrow key held: that key does nothing more (it doesn't move the
    // markup, and doesn't scroll the page either) until a key is pressed afresh.
    if (this.markHush) {
      if (e.repeat && e.key.startsWith("Arrow")) return true;
      this.markHush = false;
      if (!sel && !this.markPending) return false;
    }
    // (While the page is shown again after a change the tool reads as off for a moment: markPending says it's still ours.)
    if (!this.markPending && (!sel || !EDIT_MODES.has(this.markupMode))) return false;
    const by = this.#nudgeBy(e.key, e.shiftKey ? 10 : 1);
    if (e.key === "Escape") {
      // The presses made so far are written; the markup is then let go (also when
      // a change is being written: it isn't picked up again after it), and a key
      // still held does nothing more.
      this.markQueued = null;
      if (this.markNudge) this._markFlush();
      if (this.markPending) this.markDropped = this.markHush = true;
      this._markLetGo();
      return true;
    }
    if (this.markBusy || this.markPending || !this._markShapes()) {   // still writing the last change
      if (by && !this.markDropped) this.markQueued = [(this.markQueued?.[0] || 0) + by[0], (this.markQueued?.[1] || 0) + by[1]];
      return true;                                                   // (and no key scrolls the page meanwhile)
    }
    if (e.key === "Delete" || e.key === "Backspace") return this._markDelete();
    if (by) { this.#nudge(by); return true; }
    if (e.key === "Enter" && sel.shape.kind === "callout") { this._markFlush(); this._markEditText(); return true; }
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
      return `<circle class="rd-markgrip" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5"/>`;
    }).join("");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "rd-markedit" + (shape ? " rd-markmoving" : ""));
    svg.innerHTML = `<g class="rd-markline">${outline}</g>${grips}`;
    view.div.append(svg);
  }
};
