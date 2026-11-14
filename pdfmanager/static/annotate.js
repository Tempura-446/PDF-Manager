// The reader's annotate bar: pdf.js's editors (highlight, text, drawing,
// pictures) and the reader's own markup written by the server (notes,
// underline, strikeout, shapes, stamps: markup.py), the Comments tab,
// pictures and links on the page (pageimages.py) and Redact (marks, then
// Black out: redactcontent.py). A part of Reader (see Annotating below).

import { COLORS, el, isTyping, pdfjs, store, svg } from "./readerkit.js";

// Notes, underline, strikeout and shapes: the reader's own tools, written into
// the file by the server (markup.py) as standard annotations.
const MARKUP = new Set(["NOTE", "UNDERLINE", "STRIKEOUT", "SHAPE", "IMAGES", "LINKS", "CERTSIGN", "STAMPS", "REDACT"]);
const KIND_NAMES = { Text: "Note", FreeText: "Text", Highlight: "Highlight", Underline: "Underline", StrikeOut: "Strikeout",
  Squiggly: "Squiggly", Square: "Rectangle", Circle: "Ellipse", Line: "Line", Polygon: "Shape", PolyLine: "Lines",
  Ink: "Drawing", Stamp: "Stamp", Caret: "Insert", FileAttachment: "Attachment", Sound: "Sound" };

/** A box on the page (PDF coordinates) as [x0, y0, x1, y1] on screen, within the page. */
function viewRect(viewport, [x0, y0, x1, y1]) {
  const [a, b] = viewport.convertToViewportPoint(x0, y0), [c, d] = viewport.convertToViewportPoint(x1, y1);
  return [a, b, c, d];
}


/** Reader's annotating, as a class it extends (see Measuring in
 *  measuring.js): the annotate bar and its tools (pdf.js's editors and the
 *  reader's own markup), colours and sizes, notes, stamps and shapes, the
 *  Comments tab, pictures and links on the page, and Redact. */
export const Annotating = (Base) => class extends Base {
  // ---- annotating and signing ------------------------------------------------------
  openAnnotate() {
    if (!this.doc) return;
    // Over the find bar: it gives way, and comes back when this bar closes.
    const wasHidden = this.annotbar.hidden;
    this.annotbar.hidden = false;
    if (!this.findbar.hidden) {
      this.gaveWay = null;
      this.closeFind();
      this.gaveWay = { bar: "find" };
    } else if (wasHidden) this.gaveWay = null;
    this._stepAside();
    this.annotateBtn.setAttribute("aria-pressed", "true");
    this._refresh({ notify: false });
    this._showEditMode(this.editMode || "NONE");
  }
  closeAnnotate() {
    if (this.annotbar.hidden) return;
    this._closeToolsMenu();
    this.annotbar.hidden = true;
    this.annotateBtn.setAttribute("aria-pressed", "false");
    this._refresh({ notify: false });
    if (this.editMode && this.editMode !== "NONE") this._setEditMode("NONE");
    this._endHint();
    const back = this.gaveWay?.bar === "find" && this.doc;
    this.gaveWay = null;
    if (back) {  // the find bar it opened over
      this.findbar.hidden = false;
      this.findBtn.setAttribute("aria-pressed", "true");
      this.findInput.focus();
      if (this.findInput.value) this._find("");
    }
    this._stepAside();
  }

  _modeName(mode) {
    return Object.entries(pdfjs.AnnotationEditorType).find(([, v]) => v === mode)?.[0] || "NONE";
  }

  /** With Select on (the annotate bar open, no tool), a click on an
   *  annotation this reader can edit picks it, to move, resize or delete:
   *  one placed here, and one already in the file (also after another tab
   *  was shown and this one came back, when they are all in the file). pdf.js
   *  picks those only on a double-click (its "switchannotationeditormode"
   *  with the annotation's id); a click asks it the same, without starting to
   *  type in a text box. Links, form fields and selecting text are left as
   *  they were. */
  _pickOnClick(e) {
    if (e.button !== 0 || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
    if (!this.doc || !this.uiManager || this.annotbar.hidden || this.editMode !== "NONE" || this.textEditMode
      || this.markupMode || this.measureMode || this.container.classList.contains("hand")) return;
    if (e.target.closest?.("a, input, select, textarea, button, [contenteditable='true'], .linkAnnotation, .widgetAnnotation, .popup")) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;  // words were being selected
    const n = Number(e.target.closest?.(".page")?.dataset.pageNumber);
    const view = n && this.pdfViewer.getPageView(n - 1);
    if (!view) return;
    const under = (box) => {
      if (!box || box.hidden || box.closest("[hidden]")) return false;
      const r = box.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    };
    let hit = null;  // (the last one found is drawn on top)
    // Editors (placed here, or changed and not saved yet) ...
    for (const ed of this.uiManager.getEditors(n - 1)) {
      if (!ed.isEmpty() && under(ed.div)) hit = { mode: ed.mode, editId: ed.annotationElementId || ed.id };
    }
    // ... and annotations in the file that pdf.js can edit.
    if (!hit) {
      for (const a of view.annotationLayer?.annotationLayer?.getEditableAnnotations() || []) {
        if (a.annotationEditorType !== undefined && under(a.container)) hit = { mode: a.annotationEditorType, editId: a.data.id };
      }
    }
    if (!hit || this._modeName(hit.mode) === "NONE") return;
    e.preventDefault();
    e.stopPropagation();
    // (pdf.js turns on the annotation's tool, makes the page's editors and
    // selects the one with this id.)
    this._setEditMode(this._modeName(hit.mode), false, { editId: hit.editId, mustEnterInEditMode: false, editComment: false });
  }

  /** Switch the pdf.js editing tool: NONE (select), HIGHLIGHT, FREETEXT, INK
   *  or STAMP; or EDITTEXT, this reader's own tool for changing words. */
  _setEditMode(name, create = false, pick = null) {
    if (!this.pdfViewer || !this.doc) return;
    const editText = name === "EDITTEXT";
    this._endHint();  // the last tool's hint goes with it
    if (editText && !this.textEditHinted) {
      this.textEditHinted = true;
      this._hint("Click a line to change it, Shift+click for its whole paragraph, or click an empty spot to add words. Old words can stay in the file underneath — to remove text for good, use Redact.", 8000);
    }
    const markup = MARKUP.has(name), custom = editText || markup;
    if (name !== "NONE" || custom) this.measureTool(null);
    this.textEditMode = editText;
    this.markupMode = markup ? name : null;
    this.#cancelMarkup();
    this.root.classList.toggle("rd-edittext", editText);
    this.root.classList.toggle("rd-markup", markup);
    this.root.dataset.markup = markup ? name.toLowerCase() : "";
    this.pickedImage = null;
    this.#objectBoxes();
    if (custom) name = "NONE";
    const mode = pdfjs.AnnotationEditorType[name];
    if (mode === undefined) return;
    if (this.annotbar.hidden && (name !== "NONE" || custom)) this.openAnnotate();
    // Back to no tool with a picked annotation still focused (Esc after
    // picking one): pdf.js would see it focused again as the page's editors
    // are put away, pick it again, and turn its tool back on.
    if (mode === pdfjs.AnnotationEditorType.NONE && document.activeElement?.closest?.(".annotationEditorLayer")) {
      this.container.focus({ preventScroll: true, focusVisible: false });
    }
    try {
      if (!(custom && this.pdfViewer.annotationEditorMode === mode)) this.pdfViewer.annotationEditorMode = { mode, ...pick };
    } catch (e) {
      // (Turning the tools off on a document that has just been replaced, e.g. after signing, isn't news.)
      if (!custom) { if (name !== "NONE") this.toast("Annotating isn't available for this document."); return; }
    }
    this._showEditMode(name);
    // Words selected before choosing Underline or Strike out get it straight away.
    if (this.markupMode === "UNDERLINE" || this.markupMode === "STRIKEOUT") this.#markSelection();
    if (this.markupMode === "REDACT") this.#redactSelection();
    // "Picture" asks for an image file straight away.
    if (create && this.uiManager) setTimeout(() => this.uiManager.updateParams(pdfjs.AnnotationEditorParamsType.CREATE, null), 50);
  }

  _showEditMode(name) {
    if (name === "NONE" && this.textEditMode) name = "EDITTEXT";
    if (name === "NONE" && this.markupMode) name = this.markupMode;
    this.editMode = name;
    this._undoButtons();
    for (const btn of this.annotTools) btn.setAttribute(this.moreTools.includes(btn) ? "aria-checked" : "aria-pressed", String(btn.dataset.mode === name));
    this.#showMoreTools(name);
    const sizes = name === "FREETEXT" ? [[10, "10 pt"], [12, "12 pt"], [14, "14 pt"], [18, "18 pt"], [24, "24 pt"], [36, "36 pt"]]
      : name === "INK" ? [[1, "Thin"], [2, "Fine"], [4, "Medium"], [8, "Thick"], [14, "Marker"]]
      : name === "HIGHLIGHT" ? [[8, "Thin"], [12, "Normal"], [20, "Wide"]]
      : name === "SHAPE" ? [[1, "Thin"], [2, "Medium"], [4, "Thick"], [8, "Heavy"]] : [];
    const key = { FREETEXT: "text", INK: "ink", HIGHLIGHT: "mark", SHAPE: "shape", NOTE: "note", UNDERLINE: "line", STRIKEOUT: "line" }[name];
    this.shapeSelect.hidden = name !== "SHAPE";
    this.stampSelect.hidden = this.stampDatedLabel.hidden = name !== "STAMPS";
    const chosen = this.annotParams?.size?.[key] ?? (key === "shape" ? 2 : undefined);
    this.sizeSelect.replaceChildren(...sizes.map(([v, label]) => el("option", { value: v, selected: v === chosen }, label)));
    this.sizeSelect.hidden = !sizes.length;
    this.root.querySelector(".rd-swatches").hidden = !key;
    this.#showRedactBar(name);
  }

  /** The More tools button: "More tools", or the tool from its menu that's in use. */
  #showMoreTools(name = this.editMode) {
    const on = this.moreTools.find((t) => t.dataset.mode === name);
    const label = on ? on.querySelector("span").textContent : "More tools";
    this.moreToolsBtn.innerHTML = (on ? on.querySelector("svg").outerHTML : svg("more")) + `<span>${label}</span>` + svg("down");
    this.moreToolsBtn.classList.toggle("on", !!on);
    this.moreToolsBtn.setAttribute("aria-label", on ? `More tools (in use: ${label})` : "More tools");
  }

  _toggleToolsMenu(focus = "checked") {
    if (!this.moreToolsMenu.hidden) { this._closeToolsMenu(); return; }
    const at = this.moreToolsBtn.getBoundingClientRect(), box = this.root.getBoundingClientRect();
    this.moreToolsMenu.style.top = `${at.bottom - box.top + 6}px`;
    this.moreToolsMenu.style.left = `${Math.max(8, Math.min(at.left - box.left, box.width - 268))}px`;
    this.moreToolsMenu.style.right = "auto";
    this.moreToolsMenu.hidden = false;
    this.moreToolsBtn.setAttribute("aria-expanded", "true");
    const items = this.moreTools;
    (focus === "last" ? items.at(-1) : items.find((t) => t.getAttribute("aria-checked") === "true") || items[0]).focus();
  }
  _closeToolsMenu(refocus = false) {
    if (this.moreToolsMenu.hidden) return;
    this.moreToolsMenu.hidden = true;
    this.moreToolsBtn.setAttribute("aria-expanded", "false");
    if (refocus) this.moreToolsBtn.focus();
  }

  _applyAnnotParams() {
    this.annotParams ||= store.get("pdfm-annot", { color: 1, size: { text: 12, ink: 2, mark: 12 } });
    this._setColor(this.annotParams.color, false);
    for (const [key, value] of Object.entries(this.annotParams.size)) this._setSize(value, key, false);
  }

  _setColor(index, remember = true) {
    const [, ink, mark] = COLORS[index] || COLORS[1];
    for (const sw of this.swatches) sw.setAttribute("aria-pressed", String(+sw.dataset.index === index));
    const P = pdfjs.AnnotationEditorParamsType;
    if (this.uiManager) {
      // Applies to the selected annotations, or else to new ones.
      this.uiManager.updateParams(P.FREETEXT_COLOR, ink);
      this.uiManager.updateParams(P.INK_COLOR, ink);
      this.uiManager.updateParams(P.HIGHLIGHT_COLOR, mark);
    }
    if (remember) { this.annotParams.color = index; store.set("pdfm-annot", this.annotParams); }
  }

  _setSize(value, key = { FREETEXT: "text", INK: "ink", HIGHLIGHT: "mark", SHAPE: "shape" }[this.editMode], remember = true) {
    if (!key || !value) return;
    const P = pdfjs.AnnotationEditorParamsType;
    const type = { text: P.FREETEXT_SIZE, ink: P.INK_THICKNESS, mark: P.HIGHLIGHT_THICKNESS }[key];
    if (this.uiManager && type !== undefined) this.uiManager.updateParams(type, value);
    if (remember) { this.annotParams.size[key] = value; store.set("pdfm-annot", this.annotParams); }
  }

  // ---- notes, underline, strikeout, shapes and comments (markup.py) ---------------
  /** The colour picked in the annotation bar, as #rrggbb. */
  #markupColor() {
    const [, ink] = COLORS[this.annotParams?.color ?? 1] || COLORS[1];
    return ink;
  }

  /** Where a pointer event is on a page: { n, pt (PDF), view, box } or null. */
  #pointOnPage(e) {
    const pageEl = e.target.closest?.(".page") || document.elementFromPoint(e.clientX, e.clientY)?.closest?.(".page");
    const n = Number(pageEl?.dataset.pageNumber);
    const view = n ? this.pdfViewer.getPageView(n - 1) : null;
    if (!view?.div || !view.viewport) return null;
    const box = view.div.getBoundingClientRect();
    const x = e.clientX - box.left - view.div.clientLeft, y = e.clientY - box.top - view.div.clientTop;
    const pt = view.viewport.convertToPdfPoint(x, y);
    return Number.isFinite(pt[0]) && Number.isFinite(pt[1]) ? { n, pt, view, x, y } : null;
  }

  /** Send ``changes`` (see markup.py) to be written into the document. */
  _applyMarkup(changes, failure = "Could not add that: ") {
    return this._changeDocument((bytes, tab) => this._post("/api/markup", bytes, tab, {
      changes: JSON.stringify(changes), author: this.authorInput.value.trim() }), { failure });
  }

  #cancelMarkup() {
    this.shapeDraft?.el.remove();
    this.shapeDraft = null;
    this.noteBox?.remove();
    this.noteBox = null;
  }

  _markupDown(e) {
    const mode = this.markupMode;
    if (!mode || e.button !== 0 || mode === "UNDERLINE" || mode === "STRIKEOUT" || (mode === "REDACT" && this.#redactByText())) return;
    if (e.target.closest?.(".rd-notebox, .rd-objbox, .rd-objtools, a, input, textarea, select, button")) return;
    const at = this.#pointOnPage(e);
    if (mode === "IMAGES") { if (at) this._pickImage(null); return; }  // a click beside the pictures lets go
    if (!at) return;
    e.preventDefault();
    e.stopPropagation();
    if (mode === "NOTE") { this.#openNoteBox(at); return; }
    if (mode === "LINKS") { this.#drawLinkBox(e, at); return; }
    if (mode === "CERTSIGN") { this.#dragBox(at, "Drag a box where the signature goes.", (rect) => this._signWithId(at.n, rect)); return; }
    if (mode === "STAMPS") { this.#placeStamp(at); return; }
    if (mode === "REDACT") { this.#dragBox(at, "Drag a box over what should be blacked out.", (rect) => this.#addRedactions(at.n, [rect])); return; }
    // SHAPE: drag out the shape; it's drawn on screen until the file has it.
    this.#cancelMarkup();
    const svgEl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svgEl.setAttribute("class", "rd-shapedraft");
    at.view.div.append(svgEl);
    this.shapeDraft = { el: svgEl, start: at, end: at, kind: this.shapeSelect.value, pointer: e.pointerId };
    this.container.setPointerCapture?.(e.pointerId);
  }

  _markupMove(e) {
    const d = this.shapeDraft;
    if (!d || e.pointerId !== d.pointer) return;
    const view = d.start.view, box = view.div.getBoundingClientRect();
    let x = e.clientX - box.left - view.div.clientLeft, y = e.clientY - box.top - view.div.clientTop;
    const line = d.kind === "arrow" || d.kind === "line";
    if (e.shiftKey) {  // a square or circle; a line at 45° steps
      const dx = x - d.start.x, dy = y - d.start.y;
      if (line) {
        const a = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4), len = Math.hypot(dx, dy);
        x = d.start.x + len * Math.cos(a); y = d.start.y + len * Math.sin(a);
      } else {
        const side = Math.max(Math.abs(dx), Math.abs(dy));
        x = d.start.x + Math.sign(dx || 1) * side; y = d.start.y + Math.sign(dy || 1) * side;
      }
    }
    d.end = { x, y, pt: view.viewport.convertToPdfPoint(x, y) };
    const color = this.#markupColor(), width = Math.max(1, (this.annotParams?.size?.shape || 2) * view.viewport.scale);
    const [x0, y0, x1, y1] = [Math.min(d.start.x, x), Math.min(d.start.y, y), Math.max(d.start.x, x), Math.max(d.start.y, y)];
    const shape = d.kind === "ellipse" ? `<ellipse cx="${(x0 + x1) / 2}" cy="${(y0 + y1) / 2}" rx="${(x1 - x0) / 2}" ry="${(y1 - y0) / 2}"/>`
      : line ? `<line x1="${d.start.x}" y1="${d.start.y}" x2="${x}" y2="${y}"${d.kind === "arrow" ? ' marker-end="url(#rd-arrowhead)"' : ""}/>`
      : `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"${d.kind === "cloud" ? ' stroke-dasharray="2 3" rx="6"' : ""}/>`;
    d.el.innerHTML = `<defs><marker id="rd-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">`
      + `<path d="M0 0 10 5 0 10z" fill="${color}"/></marker></defs><g fill="none" stroke="${color}" stroke-width="${width}">${shape}</g>`;
  }

  _markupUp(e) {
    const mode = this.markupMode;
    if (!mode) return;
    if (mode === "UNDERLINE" || mode === "STRIKEOUT") {
      if (e.target.closest?.(".rd-container")) setTimeout(() => this.#markSelection(), 0);
      return;
    }
    if (mode === "REDACT") {
      if (this.#redactByText() && e.target.closest?.(".rd-container")) setTimeout(() => this.#redactSelection(), 0);
      return;
    }
    const d = this.shapeDraft;
    if (!d || e.pointerId !== d.pointer) return;
    this._markupMove(e);
    this.shapeDraft = null;
    const { start, end, kind } = d;
    if (Math.hypot(end.x - start.x, end.y - start.y) < 4) { d.el.remove(); return; }  // a click, not a drag
    const page = start.n, color = this.#markupColor(), width = this.annotParams?.size?.shape || 2;
    const change = kind === "arrow" || kind === "line"
      ? { op: "add", page, kind, points: [start.pt, end.pt], color, width }
      : { op: "add", page, kind, rect: [start.pt[0], start.pt[1], end.pt[0], end.pt[1]], color, width };
    this._applyMarkup([change]).finally(() => d.el.remove());
  }

  // -- stamps --
  /** A stamp where the page was clicked (the usual size), or in the box
   *  dragged out. */
  #placeStamp(at) {
    const view = at.view, draft = el("div", { class: "rd-linkdraft rd-stampdraft", style: `left:${at.x}px;top:${at.y}px;width:0;height:0` });
    view.div.append(draft);
    const box = view.div.getBoundingClientRect(), ox = box.left + view.div.clientLeft, oy = box.top + view.div.clientTop;
    let end = [at.x, at.y];
    const move = (ev) => {
      end = [ev.clientX - ox, ev.clientY - oy];
      Object.assign(draft.style, { left: Math.min(at.x, end[0]) + "px", top: Math.min(at.y, end[1]) + "px",
        width: Math.abs(end[0] - at.x) + "px", height: Math.abs(end[1] - at.y) + "px" });
    };
    const up = async () => {
      window.removeEventListener("pointermove", move);
      draft.remove();
      const dated = this.stampDated.checked, s = view.viewport.scale;
      let a = [at.x, at.y], b = end;
      if (Math.abs(end[0] - at.x) < 12 || Math.abs(end[1] - at.y) < 8) {  // a click: the usual size, centred there
        const w = 160 * s, h = (dated ? 50 : 42) * s;
        a = [at.x - w / 2, at.y - h / 2]; b = [at.x + w / 2, at.y + h / 2];
      }
      let label = this.stampSelect.value;
      if (!label) {
        const words = el("input", { type: "text", value: store.get("pdfm-stamp-own", ""), placeholder: "e.g. PAID", "aria-label": "Stamp words", maxlength: "60" });
        setTimeout(() => words.focus(), 0);
        if (await this._dialog("Your own stamp", [el("label", { class: "rd-field" }, "Words on the stamp", words)],
          [["cancel", "Cancel"], ["ok", "Stamp", true]]) !== "ok" || !words.value.trim()) return;
        label = words.value.trim();
        store.set("pdfm-stamp-own", label);
      }
      store.set("pdfm-stamp", this.stampSelect.value);
      store.set("pdfm-stamp-dated", dated);
      const p = view.viewport.convertToPdfPoint(...a), q = view.viewport.convertToPdfPoint(...b);
      const rect = [Math.min(p[0], q[0]), Math.min(p[1], q[1]), Math.max(p[0], q[0]), Math.max(p[1], q[1])];
      this._applyMarkup([{ op: "add", page: at.n, kind: "stamp", rect, label, dated }], "Could not stamp the page: ");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  }
  // -- end of stamps --

  /** Underline or strike out the selected words. */
  #markSelection() {
    const kind = { UNDERLINE: "underline", STRIKEOUT: "strikeout" }[this.markupMode];
    const sel = window.getSelection();
    if (!kind || !sel || sel.isCollapsed || !sel.rangeCount) return;
    const pages = new Map();
    for (const r of sel.getRangeAt(0).getClientRects()) {
      if (r.width < 1 || r.height < 1) continue;
      const at = this.#pointOnPage({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, target: document.body });
      if (!at || !this.container.contains(at.view.div)) continue;
      const box = at.view.div.getBoundingClientRect(), dx = box.left + at.view.div.clientLeft, dy = box.top + at.view.div.clientTop;
      const a = at.view.viewport.convertToPdfPoint(r.left - dx, r.top - dy), b = at.view.viewport.convertToPdfPoint(r.right - dx, r.bottom - dy);
      const rect = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
      if (!pages.has(at.n)) pages.set(at.n, []);
      pages.get(at.n).push(rect);
    }
    if (!pages.size) return;
    sel.removeAllRanges();
    const color = this.#markupColor();
    this._applyMarkup([...pages].map(([page, rects]) => ({ op: "add", page, kind, rects, color })));
  }

  /** A box to type a note into, where the page was clicked. */
  #openNoteBox(at) {
    this.#cancelMarkup();
    const text = el("textarea", { rows: 4, placeholder: "Your comment", "aria-label": "Note" });
    const add = el("button", { type: "button", class: "rd-btn primary" }, "Add note");
    const cancel = el("button", { type: "button", class: "rd-btn" }, "Cancel");
    const box = el("div", { class: "rd-notebox", style: `left:${at.x}px;top:${at.y}px` }, text, el("div", { class: "rd-notebtns" }, cancel, add));
    at.view.div.append(box);
    this._keepInColumn(box, at);
    this.noteBox = box;
    const done = () => {
      const words = text.value.trim();
      this.#cancelMarkup();
      if (words) this._applyMarkup([{ op: "add", page: at.n, kind: "note", at: at.pt, text: words, color: "#f5c400" }]);
    };
    add.addEventListener("click", done);
    cancel.addEventListener("click", () => this.#cancelMarkup());
    this.noteCommit = () => { if (box.isConnected) done(); this.noteCommit = null; };
    text.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") this.#cancelMarkup();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) done();
      // Ctrl+S here: the note goes in, then the document is saved (as anywhere else in the reader).
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !e.altKey) { e.preventDefault(); this._saveKey(e); }
    });
    setTimeout(() => text.focus(), 0);
  }

  // ---- the Comments tab --------------------------------------------------------------
  /** Every annotation with what's written on it, by page, replies under what they answer. */
  async _listComments() {
    const doc = this.doc, list = this.commentsList;
    if (!doc || this.commentsPanel.hidden) return;
    const seq = (this.commentsSeq = (this.commentsSeq || 0) + 1);
    list.replaceChildren(el("p", { class: "rd-muted" }, "Reading comments…"));
    const items = [];
    for (let n = 1; n <= doc.numPages; n++) {
      let annots = [];
      try { annots = await (await doc.getPage(n)).getAnnotations({ intent: "display" }); } catch (e) { /* skip the page */ }
      if (seq !== this.commentsSeq || doc !== this.doc) return;
      for (const a of annots) {
        if (!KIND_NAMES[a.subtype] || a.replyType === "Group") continue;
        items.push({ page: n, id: a.id, kind: a.subtype === "Line" && a.lineEndings?.[1] && a.lineEndings[1] !== "None" ? "Arrow" : KIND_NAMES[a.subtype],
          text: a.contentsObj?.str || "", author: a.titleObj?.str || "", date: a.modificationDate || a.creationDate,
          reply: a.inReplyTo || null, rect: a.rect, color: a.color });
      }
    }
    const byId = new Map(items.map((it) => [it.id, it]));
    const top = items.filter((it) => !it.reply || !byId.has(it.reply));
    for (const it of items) if (it.reply && byId.has(it.reply)) (byId.get(it.reply).replies ||= []).push(it);
    const fresh = this._changed() ? [el("p", { class: "rd-muted" }, "Highlights and drawings not saved yet show here once you save or add a note.")] : [];
    if (!top.length) {
      list.replaceChildren(...fresh, el("p", { class: "rd-muted" }, "No comments or markup yet. Use Note, Underline, Strike out or Shapes in Annotate & sign."));
      return;
    }
    list.replaceChildren(...fresh, ...top.map((it) => this.#commentCard(it)));
  }

  // -- comment summary and XFDF --
  async #commentRequest(what, extra = null) {
    await this.chain;
    const tab = this.activeTab;
    const body = new FormData();
    body.append("file", new Blob([await this._currentBytes()], { type: "application/pdf" }), "file.pdf");
    body.append("password", tab?.password || "");
    body.append("name", this.name || "document.pdf");
    if (extra) body.append("xfdf", extra, extra.name);
    const res = await fetch(`/api/comments/${what}`, { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return res;
  }

  /** A PDF listing the comments, opened in a new tab. */
  async _commentSummary() {
    if (!this.doc) return;
    try {
      const res = await this.#commentRequest("summary");
      const stem = (this.name || "document").replace(/\.pdf$/i, "");
      await this.openBytes(new Uint8Array(await res.arrayBuffer()), `${stem} — comments.pdf`);
    } catch (e) { this.toast("Couldn't make the summary: " + (e.message || e)); }
  }

  async _exportComments() {
    if (!this.doc) return;
    try {
      const res = await this.#commentRequest("export");
      const n = Number(res.headers.get("X-Pdfm-Count")) || 0;
      if (!n) { this.toast("This document has no comments to export."); return; }
      const stem = (this.name || "document").replace(/\.pdf$/i, "");
      await this._saveBytes(new Uint8Array(await res.arrayBuffer()), `${stem}.xfdf`, () => {}, this.activeTab?.fileId);
    } catch (e) { this.toast("Couldn't export the comments: " + (e.message || e)); }
  }

  _importComments(file) {
    return this._changeDocument(async (bytes, tab) => {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      body.append("password", tab.password || "");
      body.append("xfdf", file, file.name);
      return fetch("/api/comments/import", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    }, {
      failure: "Couldn't add the comments: ",
      after: (res) => {
        const n = Number(res.headers.get("X-Pdfm-Added")) || 0, skipped = res.headers.get("X-Pdfm-Skipped");
        this.toast(n ? `Added ${n} comment${n === 1 ? "" : "s"}${skipped ? ` (some kinds couldn't be added: ${skipped})` : ""}.`
          : "Nothing to add: these comments are already here.");
        this._listComments();
      },
    });
  }
  // -- end of comment summary and XFDF --

  #commentCard(it, isReply = false) {
    const date = (raw) => {
      const d = raw && pdfjs.PDFDateString.toDateObject(raw);
      return d ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
    };
    const swatch = it.color ? `rgb(${[...it.color].join(",")})` : "transparent";
    const head = el("div", { class: "rd-commenthead" },
      el("i", { class: "rd-commentdot", style: `background:${swatch}` }),
      el("b", {}, isReply ? "Reply" : it.kind), el("span", {}, it.author ? " · " + it.author : ""),
      el("span", { class: "rd-muted" }, ` · p. ${this.labels?.[it.page - 1] || it.page}`));
    const body = el("p", { class: "rd-commenttext" + (it.text ? "" : " rd-muted") }, it.text || "No comment");
    const when = el("div", { class: "rd-muted rd-commentdate" }, date(it.date));
    const btn = (label, fn) => { const b = el("button", { type: "button", class: "rd-linkbtn" }, label); b.addEventListener("click", (e) => { e.stopPropagation(); fn(); }); return b; };
    const card = el("article", { class: "rd-comment" + (isReply ? " reply" : ""), tabindex: "0" }, head, body, when);
    const ask = (label, value, apply) => {
      card.querySelector(".rd-commentedit")?.remove();
      const area = el("textarea", { rows: 3, "aria-label": label }, value);
      const ok = el("button", { type: "button", class: "rd-btn primary" }, label);
      const box = el("div", { class: "rd-commentedit" }, area, el("div", { class: "rd-notebtns" }, btn("Cancel", () => box.remove()), ok));
      ok.addEventListener("click", (e) => { e.stopPropagation(); apply(area.value.trim()); });
      area.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ok.click(); if (e.key === "Escape") box.remove(); });
      card.append(box);
      area.focus();
    };
    card.append(el("div", { class: "rd-commentbtns" },
      isReply ? null : btn("Reply", () => ask("Reply", "", (text) => text && this._applyMarkup([{ op: "reply", page: it.page, id: it.id, text }], "Could not reply: "))),
      btn(it.text ? "Edit" : "Add comment", () => ask("Save", it.text, (text) => this._applyMarkup([{ op: "text", page: it.page, id: it.id, text }], "Could not change the comment: "))),
      btn("Delete", async () => {
        const what = isReply ? "this reply" : `this ${it.kind.toLowerCase()}${it.replies?.length ? " and its replies" : ""}`;
        if (await this.ask(`Delete ${what}?`, "Undo (Ctrl+Z) brings it back until you save.",
          [["cancel", "Cancel"], ["delete", "Delete", true]]) === "delete") {
          this._applyMarkup([{ op: "delete", page: it.page, id: it.id }], "Could not delete it: ");
        }
      })));
    card.addEventListener("click", () => this.#showComment(it));
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === card) this.#showComment(it); });
    for (const r of it.replies || []) card.append(this.#commentCard(r, true));
    return card;
  }

  /** Scroll to an annotation and flash it. */
  #showComment(it) {
    const [x0, y0, x1, y1] = it.rect || [0, 0, 0, 0];
    this.pdfViewer.scrollPageIntoView({ pageNumber: it.page, destArray: [null, { name: "XYZ" }, x0 - 40, y1 + 60, null] });
    setTimeout(() => {
      const view = this.pdfViewer.getPageView(it.page - 1);
      if (!view?.div || !view.viewport) return;
      const [a, b, c, d] = viewRect(view.viewport, [x0, y0, x1, y1]);
      const flash = el("div", { class: "rd-flash", style: `left:${Math.min(a, c) - 4}px;top:${Math.min(b, d) - 4}px;width:${Math.abs(c - a) + 8}px;height:${Math.abs(d - b) + 8}px` });
      view.div.append(flash);
      setTimeout(() => flash.remove(), 1600);
    }, 150);
  }

  // ---- pictures and links on the page (pageimages.py, markup.py) -------------------
  /** Pictures or Links mode: outlines over the pages' pictures or links. */
  #objectBoxes() {
    this.viewerEl?.querySelectorAll(".rd-objbox, .rd-objtools, .rd-linkdraft").forEach((n) => n.remove());
    if (this.markupMode === "CERTSIGN") this._hint("Drag a box where your signature should appear, then choose your Digital ID.", 5000);
    if ((this.markupMode !== "IMAGES" && this.markupMode !== "LINKS") || !this.doc) return;
    for (let i = 0; i < this.pdfViewer.pagesCount; i++) {
      if (this.pdfViewer.getPageView(i)?.canvas?.width) this._drawObjectBoxes(i + 1);
    }
    if (this.markupMode === "IMAGES" && !this.imagesHinted) {
      this.imagesHinted = true;
      this._hint("Click a picture to pick it: drag it to move it, drag a corner to resize it (Shift: freely), or replace or delete it.", 6000);
    }
  }

  /** The pictures of the shown document, by page (asked once per change of it). */
  #imagesOf(tab) {
    if (tab.imagesFor !== (tab.version || 0) || !tab.images) {
      tab.imagesFor = tab.version || 0;
      tab.images = (async () => {
        const body = new FormData();
        body.append("file", new Blob([await this._fullBytes()], { type: "application/pdf" }), "file.pdf");
        body.append("password", tab.password || "");
        const res = await fetch("/api/images/list", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
        return res.ok ? (await res.json()).pages || {} : {};
      })().catch(() => ({}));
    }
    return tab.images;
  }

  /** Where a link goes: { url } or { page }. */
  async #linkTarget(a) {
    if (a.url || a.unsafeUrl) return { url: a.url || a.unsafeUrl };
    try {
      let dest = a.dest;
      if (typeof dest === "string") dest = await this.doc.getDestination(dest);
      if (Array.isArray(dest) && dest[0]) {
        const index = typeof dest[0] === "object" ? await this.doc.getPageIndex(dest[0]) : dest[0];
        return { page: index + 1 };
      }
    } catch (e) { /* somewhere this reader can't name */ }
    return {};
  }

  async _drawObjectBoxes(n) {
    const view = this.pdfViewer.getPageView(n - 1), tab = this.activeTab, mode = this.markupMode, doc = this.doc;
    if (!view?.div || !view.viewport || !tab) return;
    let items = [];
    if (mode === "IMAGES") items = (await this.#imagesOf(tab))[n] || [];
    else if (mode === "LINKS") {
      try { items = (await (await doc.getPage(n)).getAnnotations({ intent: "display" })).filter((a) => a.subtype === "Link"); } catch (e) { items = []; }
    }
    if (mode !== this.markupMode || doc !== this.doc) return;
    view.div.querySelectorAll(".rd-objbox, .rd-objtools").forEach((x) => x.remove());
    const vp = view.viewport;
    for (const item of items) {
      const [a, b, c, d] = viewRect(vp, item.box || item.rect);
      const box = el("div", { class: "rd-objbox" + (mode === "LINKS" ? " link" : "") + (item.editable === false ? " locked" : ""),
        style: `left:${Math.min(a, c)}px;top:${Math.min(b, d)}px;width:${Math.abs(c - a)}px;height:${Math.abs(d - b)}px` });
      if (mode === "IMAGES") {
        box.title = item.editable === false ? "This picture is drawn as part of a group, so it can't be edited here" : "Drag to move; click to pick it";
        if (item.editable !== false) for (const h of ["nw", "ne", "sw", "se"]) box.append(el("i", { class: "rd-handle " + h, "data-handle": h }));
        box.addEventListener("pointerdown", (e) => this.#grabImage(e, n, item, box));
      } else {
        const target = await this.#linkTarget(item);
        box.title = target.url ? `Goes to ${target.url}` : target.page ? `Goes to page ${target.page}` : "A link";
        box.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); });
        box.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); this.#editLink(n, item, null, target); });
      }
      view.div.append(box);
      if (mode === "IMAGES" && this.pickedImage?.page === n && this.pickedImage.index === item.index) this._pickImage({ page: n, item, box }, false);
    }
  }

  /** Pick a picture (null: none): its handles and Replace / Delete. */
  _pickImage(picked, redraw = true) {
    this.viewerEl.querySelectorAll(".rd-objbox.picked").forEach((b) => b.classList.remove("picked"));
    this.viewerEl.querySelectorAll(".rd-objtools").forEach((t) => t.remove());
    this.pickedImage = picked ? { page: picked.page, index: picked.item.index } : null;
    if (!picked) return;
    picked.box.classList.add("picked");
    const btn = (icon, label, fn) => {
      const b = el("button", { type: "button", class: "rd-btn", html: svg(icon) + `<span>${label}</span>` });
      b.addEventListener("pointerdown", (e) => e.stopPropagation());
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    };
    const tools = el("div", { class: "rd-objtools", style: `left:${picked.box.offsetLeft}px;top:${Math.max(0, picked.box.offsetTop - 42)}px` },
      btn("image", "Replace…", () => this._editImage("replace")), btn("trash", "Delete", () => this._editImage("delete")));
    picked.box.parentElement.append(tools);
    if (redraw) picked.box.focus?.();
  }

  /** Drag a picture (or one of its corners) to move (or resize) it. */
  #grabImage(e, page, item, box) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    if (item.editable === false) { this.toast("That picture is drawn as part of a group, so it can't be edited here.", 4000); return; }
    this._pickImage({ page, item, box }, false);
    const handle = e.target.dataset?.handle || null;
    const start = { x: e.clientX, y: e.clientY, left: box.offsetLeft, top: box.offsetTop, width: box.offsetWidth, height: box.offsetHeight };
    const ratio = start.width / Math.max(1, start.height);
    let moved = false;
    const move = (ev) => {
      const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
      if (!moved && Math.hypot(dx, dy) < 3) return;
      moved = true;
      let { left, top, width, height } = start;
      if (!handle) { left += dx; top += dy; }
      else {
        if (handle.includes("e")) width = start.width + dx;
        if (handle.includes("w")) width = start.width - dx;
        if (handle.includes("s")) height = start.height + dy;
        if (handle.includes("n")) height = start.height - dy;
        width = Math.max(8, width); height = Math.max(8, height);
        if (!ev.shiftKey) {  // the picture keeps its proportions
          if (Math.abs(dx) * start.height >= Math.abs(dy) * start.width) height = width / ratio; else width = height * ratio;
        }
        if (handle.includes("w")) left = start.left + start.width - width;
        if (handle.includes("n")) top = start.top + start.height - height;
      }
      Object.assign(box.style, { left: left + "px", top: top + "px", width: width + "px", height: height + "px" });
      this.viewerEl.querySelectorAll(".rd-objtools").forEach((t) => { t.style.left = left + "px"; t.style.top = Math.max(0, top - 42) + "px"; });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      if (!moved) return;
      const view = this.pdfViewer.getPageView(page - 1), vp = view.viewport;
      const [x0, y0] = vp.convertToPdfPoint(box.offsetLeft, box.offsetTop);
      const [x1, y1] = vp.convertToPdfPoint(box.offsetLeft + box.offsetWidth, box.offsetTop + box.offsetHeight);
      this.#changeImages([{ op: "move", page, index: item.index, box: [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)] }]);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  }

  async _editImage(op) {
    const picked = this.pickedImage;
    if (!picked) return;
    if (op === "delete") { this.pickedImage = null; await this.#changeImages([{ op: "delete", page: picked.page, index: picked.index }]); return; }
    const file = await new Promise((resolve) => {
      const input = el("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp,image/bmp,image/tiff", hidden: true });
      input.addEventListener("change", () => { resolve(input.files[0] || null); input.remove(); });
      input.addEventListener("cancel", () => { resolve(null); input.remove(); });
      this.root.append(input);
      input.click();
    });
    if (file) await this.#changeImages([{ op: "replace", page: picked.page, index: picked.index, picture: "picture0" }], [file]);
  }

  #changeImages(changes, files = []) {
    return this._changeDocument((bytes, tab) => {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      body.append("password", tab.password || "");
      body.append("changes", JSON.stringify(changes));
      files.forEach((f, i) => body.append("picture" + i, f, f.name || "picture"));
      return fetch("/api/images/edit", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    }, { failure: "Could not change the picture: " });
  }

  /** Links mode: drag a box on the page for a new link. */
  #drawLinkBox(e, at) {
    this.#dragBox(at, "Drag a box over what should be clickable to add a link.", (rect) => this.#editLink(at.n, null, rect));
  }

  /** Drag out a box on the page from ``at``; ``done(rect)`` gets it in PDF coordinates. */
  #dragBox(at, tooSmall, done) {
    const view = at.view, draft = el("div", { class: "rd-linkdraft", style: `left:${at.x}px;top:${at.y}px;width:0;height:0` });
    view.div.append(draft);
    const box = view.div.getBoundingClientRect(), ox = box.left + view.div.clientLeft, oy = box.top + view.div.clientTop;
    let end = [at.x, at.y];
    const move = (ev) => {
      end = [ev.clientX - ox, ev.clientY - oy];
      Object.assign(draft.style, { left: Math.min(at.x, end[0]) + "px", top: Math.min(at.y, end[1]) + "px",
        width: Math.abs(end[0] - at.x) + "px", height: Math.abs(end[1] - at.y) + "px" });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      draft.remove();
      if (Math.abs(end[0] - at.x) < 6 || Math.abs(end[1] - at.y) < 6) { this.toast(tooSmall, 3000); return; }
      const a = view.viewport.convertToPdfPoint(at.x, at.y), b = view.viewport.convertToPdfPoint(...end);
      done([Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  }

  /** Change where a link goes, or delete it; or say where a new one goes. */
  async #editLink(n, link, rect, target = {}) {
    const toWeb = el("input", { type: "radio", name: "rd-linkto", value: "web" });
    const toPage = el("input", { type: "radio", name: "rd-linkto", value: "page" });
    const url = el("input", { type: "text", value: target.url || "", placeholder: "https://example.com or name@example.com", "aria-label": "Web or email address" });
    const page = el("input", { type: "number", min: "1", max: String(this.doc.numPages), value: target.page ? String(target.page) : "", "aria-label": "Page" });
    (target.page && !target.url ? toPage : toWeb).checked = true;
    url.addEventListener("input", () => { toWeb.checked = true; });
    page.addEventListener("input", () => { toPage.checked = true; });
    setTimeout(() => (toPage.checked ? page : url).focus(), 0);
    const answer = await this._dialog(link ? "Link" : "New link", [
      el("label", { class: "rd-field" }, el("span", {}, toWeb, " Goes to a web or email address"), url),
      el("label", { class: "rd-field" }, el("span", {}, toPage, ` Goes to a page of this document (1–${this.doc.numPages})`), page)],
      link ? [["delete", "Delete the link"], ["cancel", "Cancel"], ["ok", "Save", true]] : [["cancel", "Cancel"], ["ok", "Add the link", true]]);
    if (answer === "delete") { await this._applyMarkup([{ op: "delete", page: n, id: link.id }], "Could not delete the link: "); return; }
    if (answer !== "ok") return;
    const where = toPage.checked ? { to_page: Number(page.value) } : { url: url.value.trim() };
    if (!where.url && !where.to_page) { this.toast("Say where the link goes."); return; }
    if (link) await this._applyMarkup([{ op: "link", page: n, id: link.id, ...where }], "Could not change the link: ");
    else await this._applyMarkup([{ op: "add", page: n, kind: "link", rect, ...where }], "Could not add the link: ");
  }

  // -- reader redaction --------------------------------------------------------------------
  // Mark areas to black out (drag boxes, or select words), then Apply sends
  // them to /api/redact/apply (redact.py, method "remove"): what's under them
  // is taken out of the file and black boxes are drawn. The marks belong to
  // the tab and to the version of the document they were made on.

  _buildRedact() {
    this.redactBtn = el("button", { class: "rd-btn rd-tool", type: "button", "data-mode": "REDACT", "aria-pressed": "false",
      title: "Redact: mark what should be blacked out for good, then Apply", html: svg("redact") + "<span>Redact</span>" });
    this.annotTools.push(this.redactBtn);
    this.redactHow = el("select", { class: "rd-zoom", "aria-label": "How to mark", hidden: true },
      el("option", { value: "box" }, "Draw boxes"), el("option", { value: "text" }, "Select words"));
    this.redactHow.value = store.get("pdfm-redact-how", "box");
    this.redactHow.addEventListener("change", () => {
      store.set("pdfm-redact-how", this.redactHow.value);
      this.root.dataset.redact = this.redactHow.value;
    });
    // The verb, in the Redact page's style: "Black out 3 areas", seal red.
    this.redactApplyBtn = el("button", { class: "rd-btn primary seal rd-redact-apply", type: "button", hidden: true, disabled: true,
      title: "Black out the marked areas for good", html: svg("redact") + "<span>Black out</span>" });
    this.redactApplyBtn.addEventListener("click", () => this.#applyRedactions());
  }

  #redactByText() { return this.redactHow?.value === "text"; }

  /** The tab's marks, {page: [[x0, y0, x1, y1]]} in PDF points; marks made on
   *  an earlier version of the document are dropped (its pages may have moved). */
  #redactMarks(tab = this.activeTab) {
    if (!tab) return {};
    const version = tab.version || 0;
    if (tab.redactMarks?.version !== version) tab.redactMarks = { version, pages: {} };
    return tab.redactMarks.pages;
  }

  _redactCount(tab = this.activeTab) {
    return Object.values(this.#redactMarks(tab)).reduce((n, list) => n + list.length, 0);
  }

  #showRedactBar(name) {
    const on = name === "REDACT";
    if (!this.redactHow) return;
    this.redactHow.hidden = this.redactApplyBtn.hidden = !on;
    this.root.dataset.redact = on ? this.redactHow.value : "";
    if (on) this.#showRedactCount(); else this._refresh({ notify: false });
    if (on && !this.redactHinted) {
      this.redactHinted = true;
      this._hint("Drag boxes over what should go (or choose Select words), then Black out. Click × on a mark to remove it.", 7000);
    }
  }

  #showRedactCount() {
    const n = this._redactCount();
    this.redactApplyBtn.disabled = !n;
    this.redactApplyBtn.querySelector("span").textContent = n ? `Black out ${n} ${n === 1 ? "area" : "areas"}` : "Black out";
    this.redactApplyBtn.title = n ? `Black out the ${n === 1 ? "marked area" : n + " marked areas"} for good` : "Mark something to black out first";
    this._refresh({ notify: false });
  }

  #addRedactions(n, rects) {
    const list = (this.#redactMarks()[n] ||= []);
    for (const r of rects) list.push(r.map((v) => Math.round(v * 100) / 100));
    this._drawRedactions(n);
    this.#showRedactCount();
  }

  /** Red outlines (with a remove button) over the marks on page ``n``. */
  _drawRedactions(n) {
    const view = this.pdfViewer?.getPageView(n - 1);
    if (!view?.div || !view.viewport) return;
    view.div.querySelectorAll(".rd-redactmark").forEach((x) => x.remove());
    const list = this.#redactMarks()[n] || [];
    list.forEach((rect, i) => {
      const [a, b, c, d] = viewRect(view.viewport, rect);
      const remove = el("button", { type: "button", class: "rd-redactx", title: "Remove this mark", "aria-label": "Remove this mark", html: svg("x") });
      remove.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); });
      remove.addEventListener("click", (e) => {
        e.stopPropagation();
        list.splice(i, 1);
        this._drawRedactions(n);
        this.#showRedactCount();
      });
      view.div.append(el("div", { class: "rd-redactmark", title: "Marked for redaction",
        style: `left:${Math.min(a, c)}px;top:${Math.min(b, d)}px;width:${Math.abs(c - a)}px;height:${Math.abs(d - b)}px` }, remove));
    });
  }

  /** Mark the selected words (one box per line piece, a little padded). */
  #redactSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const pages = new Map();
    for (const r of sel.getRangeAt(0).getClientRects()) {
      if (r.width < 1 || r.height < 1) continue;
      const at = this.#pointOnPage({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, target: document.body });
      if (!at || !this.container.contains(at.view.div)) continue;
      const box = at.view.div.getBoundingClientRect(), dx = box.left + at.view.div.clientLeft, dy = box.top + at.view.div.clientTop;
      const a = at.view.viewport.convertToPdfPoint(r.left - dx, r.top - dy), b = at.view.viewport.convertToPdfPoint(r.right - dx, r.bottom - dy);
      const pad = 0.75;
      const rect = [Math.min(a[0], b[0]) - pad, Math.min(a[1], b[1]) - pad, Math.max(a[0], b[0]) + pad, Math.max(a[1], b[1]) + pad];
      if (!pages.has(at.n)) pages.set(at.n, []);
      pages.get(at.n).push(rect);
    }
    if (!pages.size) return;
    sel.removeAllRanges();
    for (const [n, rects] of pages) this.#addRedactions(n, rects);
  }

  async #applyRedactions() {
    const tab = this.activeTab, marks = this.#redactMarks(tab), count = this._redactCount(tab);
    if (!tab || !count) { this.toast("Mark something first: drag a box over it, or choose Select words."); return; }
    const answer = await this._dialog(`Black out ${count === 1 ? "this area" : count + " areas"}?`, [
      el("p", {}, `This removes what's under the ${count === 1 ? "marked area" : count + " marked areas"} for good once you save: `
        + "the words, the covered parts of pictures, and comments or form fields there. The rest of the page stays as it is."),
      el("p", {}, "Until then, Undo (Ctrl+Z) brings it back. Saving makes a redacted copy beside the original, unless you choose to replace it.")],
      [["cancel", "Cancel"], ["ok", count === 1 ? "Black out 1 area" : `Black out ${count} areas`, true]]);
    if (answer !== "ok") return;
    const boxes = Object.fromEntries(Object.entries(marks).filter(([, list]) => list.length));
    await this._changeDocument((bytes, t) => this._post("/api/redact/apply", bytes, t, { boxes: JSON.stringify(boxes) }), {
      failure: "Could not redact: ",
      kind: "redact",  // Save makes a redacted copy from now on; replacing the original is asked
      after: (res) => {
        let done = {};
        try { done = JSON.parse(res.headers.get("X-Pdfm-Redacted") || "{}"); } catch (e) { /* the default words do */ }
        const pictures = Object.entries(done.pictures || {});
        let message = `Blacked out ${done.areas || count} area${(done.areas || count) === 1 ? "" : "s"}.`;
        if (pictures.length) {
          message += ` Page${pictures.length === 1 ? "" : "s"} ${pictures.map(([p]) => p).join(", ")} became `
            + `${pictures.length === 1 ? "a picture" : "pictures"} (${[...new Set(pictures.map(([, why]) => why))].join("; ")}).`;
        }
        this.toast(message + " Save a redacted copy to keep it.", 12000, [["Save redacted copy…", () => this.saveRedacted()],
          ...(tab.fileId ? [["Replace original", () => this.replaceWithRedacted()]] : [])]);
      },
    });
    if (this.markupMode === "REDACT") this.#showRedactCount();
  }
  // -- end of reader redaction --
};
