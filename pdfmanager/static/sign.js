/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// The reader's Sign: draw, type or pick a signature (kept for next time),
// trimmed to its ink, and placed as a pdf.js picture on the line it signs or
// in the middle of what's shown; and keeping a box or a bar in sight beside
// the page. A part of Reader (see Signing below). (A digital ID signature is
// the server's: certsign.py.)

import { el, pdfjs, store, svg } from "./readerkit.js";


/** Reader's drawn signatures, as a class it extends (see Measuring in
 *  measuring.js): the Sign dialog (draw, type or pick one), placing it on a
 *  line or in the middle of the view, and keeping boxes in the page's column. */
export const Signing = (Base) => class extends Base {
  /** Draw, type or pick a signature, then place it: on ``line`` when given
   *  (a printed line to sign on, from the file's facts: {page, rect: the
   *  blank or rule in PDF points, text: the label's size}), else about two
   *  inches wide in the middle of what's shown. It moves and resizes like
   *  any picture afterwards. */
  async sign({ line = null } = {}) {
    if (!this.doc || !this.uiManager) return;
    const image = await this.#signatureDialog();
    if (!image) return;
    const file = new File([image], "signature.png", { type: "image/png" });
    // Annotating first: until then the line's page may have no layer to put
    // pictures on yet (just after the file opened), which put the signature
    // in the middle of the view instead of on its line.
    if (this.annotbar.hidden) this.openAnnotate();
    // (The layer pdf.js uses for the page shown stands in when the line's page
    // has none of its own yet, e.g. in a window that was minimized.)
    const current = this.uiManager?.currentLayer;
    const onLine = line ? (await this.#editorLayer(line.page - 1)) || (current?.pageIndex === line.page - 1 ? current : null) : null;
    const layer = onLine || this.uiManager?.currentLayer;
    if (!layer) { this.toast("Scroll to the page to sign, then try again."); return; }
    // The picture's size first: pdf.js then makes the editor at its final
    // place and size and lays it out itself once the picture is in (see #placeSignature).
    const bitmap = await createImageBitmap(file).catch(() => null);
    const aspect = bitmap && bitmap.width > 0 ? bitmap.height / bitmap.width : 0;
    bitmap?.close();
    const editor = aspect > 0 ? await this.#placeSignature(layer, file, aspect, onLine ? line : null) : null;
    if (!editor || !(await this.#shownSignature(editor))) { this.toast("The signature couldn't be placed."); return; }
    this._hint(editor.signedOn === "line" ? "Signed on the line. Drag it to move it; use the corners to resize it."
      : "Drag the signature into place; use the corners to resize it.", 5000);
  }

  /** The layer pdf.js puts pictures on for page ``index``, once there:
   *  the page is brought into view to have it drawn when it isn't yet
   *  (waits up to 3 seconds; null if it never comes). */
  async #editorLayer(index) {
    const get = () => this.pdfViewer.getPageView(index)?.annotationEditorLayer?.annotationEditorLayer || null;
    if (get()) return get();
    if (index >= 0 && index < (this.pdfViewer.pagesCount || 0)) {
      const view = this.pdfViewer.getPageView(index), top = view?.div?.getBoundingClientRect(), col = this.#column();
      if (!top || top.bottom < col.top || top.top > col.bottom) this.pdfViewer.currentPageNumber = index + 1;
    }
    for (let i = 0; i < 150 && !get(); i++) await new Promise((r) => setTimeout(r, 20));
    return get();
  }

  /** Add a signature picture (``aspect``: its height over its width) to
   *  ``layer``: on the line, its foot on the rule, at the start of it, 1.6
   *  times as tall as the label's letters and no longer than the line; else
   *  2 inches wide in the middle of what's shown of the page. The editor is
   *  made where it goes, at its size, before its picture is read, the way
   *  pdf.js makes a picture that isn't centred: its own layout (when the
   *  picture comes in, and on every zoom, turn or selection after) starts
   *  from there. (Pasted in the middle at the picture's own size and then
   *  moved, as before, pdf.js's state still held the first place.) Returns
   *  the editor, or null. */
  async #placeSignature(layer, file, aspect, line) {
    const mode = pdfjs.AnnotationEditorType.STAMP;
    // The picture tool on, through the viewer (pdf.js's paste switches only
    // its editors, and the viewer then still thinks no tool is on: Select or
    // Esc did nothing, and the pages weren't drawn for editing).
    if (this.uiManager.getMode() !== mode) {
      const on = new Promise((resolve) => {
        const seen = (e) => { if (e.mode === mode) { this.eventBus.off("annotationeditormodechanged", seen); resolve(); } };
        this.eventBus.on("annotationeditormodechanged", seen);
        // (It answers in a moment; when it doesn't, pdf.js's own switch below
        // does it, rather than the signature taking seconds to appear.)
        setTimeout(() => { this.eventBus.off("annotationeditormodechanged", seen); resolve(); }, 2000);
      });
      this._setEditMode("STAMP");
      await on;
      if (!this.uiManager || !layer.div) return null;
      if (this.uiManager.getMode() !== mode) await this.uiManager.updateMode(mode);  // (the viewer didn't answer: as pdf.js's paste)
    }
    layer.updateToolbar({ mode });
    const { rotation: turn, rawDims: { pageWidth: W, pageHeight: H, pageX: px, pageY: py } } = layer.viewport;
    let rect;
    if (line && turn === 0) {
      const [x0, , x1, top] = line.rect, length = Math.max(x1 - x0, 24);
      let h = 1.6 * Math.max(line.text || 10, 4), w = h / aspect;
      if (w > length) { w = length; h = w * aspect; }
      rect = [x0, top, x0 + w, top + h];
    } else {
      line = null;
      let w = 144, h = w * aspect;  // 2 inches wide; a tall picture no taller than 1 inch
      if (h > 72) { h = 72; w = h / aspect; }
      // The middle of the part of its page that is in sight (else of the page).
      let cx = px + W / 2, cy = py + H / 2;
      const view = this.pdfViewer.getPageView(layer.pageIndex), col = this.#column();
      const page = view?.div.getBoundingClientRect();
      if (page) {
        const l = Math.max(page.left, col.left), r = Math.min(page.right, col.right), t = Math.max(page.top, col.top), b = Math.min(page.bottom, col.bottom);
        if (r > l && b > t) [cx, cy] = view.viewport.convertToPdfPoint((l + r) / 2 - page.left - view.div.clientLeft, (t + b) / 2 - page.top - view.div.clientTop);
      }
      const [dx, dy] = turn % 180 ? [h / 2, w / 2] : [w / 2, h / 2];
      rect = [cx - dx, cy - dy, cx + dx, cy + dy];
    }
    // editor.getRect turned back: where the editor's corner is, as fractions of the page.
    const [r0, r1, r2, r3] = rect, across = r2 - r0, up = r3 - r1;
    const [left, top, width, height] = {
      0: [r0 - px, H + py - r3, across, up], 90: [r0 - px, H + py - r1, up, across],
      180: [r2 - px, H + py - r1, across, up], 270: [r2 - px, H + py - r3, up, across] }[turn] || [r0 - px, H + py - r3, across, up];
    const editor = layer.createAndAddNewEditor({ offsetX: 0, offsetY: 0 }, false, { bitmapFile: file });
    if (!editor) return null;
    // (Its picture is still being read: pdf.js takes this place and size when it's in.)
    Object.assign(editor, { x: left / W, y: top / H, width: width / W, height: height / H, signedOn: line ? "line" : "default" });
    return editor;
  }

  /** Wait (up to 5 s) for a signature's picture to be in and drawn, then
   *  scroll the page column to it (a line near the foot of the page is
   *  often below). False if it never came. */
  async #shownSignature(editor) {
    const drawn = () => editor.div && !editor.div.hidden && editor.div.querySelector("canvas");
    for (let i = 0; i < 250 && !drawn(); i++) await new Promise((r) => setTimeout(r, 20));
    if (!drawn() || !editor.parent) return false;
    await new Promise((r) => requestAnimationFrame(r));
    const box = editor.div.getBoundingClientRect(), col = this.#column();
    if (box.top < col.top || box.bottom > col.bottom) this.container.scrollTop += box.top - (col.top + col.bottom) / 2 + box.height / 2;
    if (box.left < col.left || box.right > col.right) this.container.scrollLeft += box.left - (col.left + col.right) / 2 + box.width / 2;
    return true;
  }

  #signatureDialog() {
    const saved = store.get("pdfm-signature", null);
    const W = 520, H = 180, ratio = Math.min(window.devicePixelRatio || 1, 2);
    const pad = el("canvas", { class: "rd-sigpad", width: W * ratio, height: H * ratio, "aria-label": "Draw your signature here" });
    const ctx = pad.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.lineCap = ctx.lineJoin = "round"; ctx.lineWidth = 2.6; ctx.strokeStyle = "#111";
    let drawn = false, last = null;
    const point = (e) => { const r = pad.getBoundingClientRect(); return [(e.clientX - r.left) * W / r.width, (e.clientY - r.top) * H / r.height]; };
    pad.addEventListener("pointerdown", (e) => { pad.setPointerCapture(e.pointerId); last = point(e); drawn = true; ctx.beginPath(); ctx.arc(last[0], last[1], 1.2, 0, 7); ctx.fillStyle = "#111"; ctx.fill(); });
    pad.addEventListener("pointermove", (e) => {
      if (!last) return;
      const p = point(e);
      ctx.beginPath(); ctx.moveTo(...last); ctx.lineTo(...p); ctx.stroke();
      last = p;
    });
    pad.addEventListener("pointerup", () => { last = null; });
    // Focus in it as the dialog opens, to type straight away. No form
    // filling: WebView2's (turned on for passwords, see gui._allow_autofill)
    // can open its list of saved names over the box as the first letters
    // are typed, and a signature isn't a form entry.
    const typed = el("input", { type: "text", class: "rd-sigtext", placeholder: "Or type your name", "aria-label": "Type your name",
      autocomplete: "off", autofocus: true, spellcheck: "false" });
    const clear = () => { ctx.clearRect(0, 0, W, H); drawn = false; };
    typed.addEventListener("input", () => {
      clear();
      if (!typed.value.trim()) return;
      ctx.fillStyle = "#111";
      let size = 64;
      do { ctx.font = `italic ${size}px "Segoe Script", "Brush Script MT", "Lucida Handwriting", "Apple Chancery", cursive`; size -= 4; }
      while (ctx.measureText(typed.value).width > W - 40 && size > 16);
      ctx.textBaseline = "middle"; ctx.textAlign = "center";
      ctx.fillText(typed.value, W / 2, H / 2);
      drawn = true;
    });
    const pick = el("input", { type: "file", accept: "image/*", hidden: true });
    let picked = null;
    pick.addEventListener("change", async () => {
      const f = pick.files[0];
      if (!f) return;
      const img = new Image();
      img.src = URL.createObjectURL(f);
      await img.decode().catch(() => {});
      clear();
      const s = Math.min((W - 20) / img.width, (H - 20) / img.height, 1);
      ctx.drawImage(img, (W - img.width * s) / 2, (H - img.height * s) / 2, img.width * s, img.height * s);
      URL.revokeObjectURL(img.src);
      drawn = true; picked = f;
    });
    const remember = el("input", { type: "checkbox", checked: true });
    const body = [
      el("p", {}, "Draw your signature with the mouse, pen or finger, type your name, or use a picture of your signature."),
      pad,
      el("div", { class: "rd-sigrow" }, typed,
        el("button", { type: "button", class: "rd-btn", onclick: () => pick.click() }, "Picture…"),
        el("button", { type: "button", class: "rd-btn", onclick: () => { typed.value = ""; clear(); } }, "Clear"), pick),
      el("label", { class: "rd-sigremember" }, remember, "Remember this signature on this computer"),
    ];
    const buttons = [["cancel", "Cancel"], ["ok", "Place signature", true]];
    if (saved) buttons.splice(1, 0, ["saved", "Use saved signature"]);
    return this._dialog("Sign", body, buttons).then(async (answer) => {
      if (answer === "ok" && !drawn && saved) answer = "saved";  // (Enter with nothing drawn: the saved one)
      if (answer === "saved") return (await fetch(saved)).blob();
      if (answer !== "ok") return null;
      if (!drawn) { this.toast("Draw or type a signature first."); return null; }
      const blob = picked && !typed.value ? picked : await this.#trim(pad);
      if (remember.checked) {
        const reader = new FileReader();
        reader.onload = () => store.set("pdfm-signature", reader.result);
        reader.readAsDataURL(blob);
      }
      return blob;
    });
  }

  /** The part of the pages the reader can see now: the page column, under
   *  the bars and left of a docked file panel (and its scrollbar). */
  #column() {
    const c = this.container.getBoundingClientRect();
    return { left: c.left + 8, top: c.top + 8, right: c.left + this.container.clientWidth - 8, bottom: c.top + this.container.clientHeight - 8 };
  }
  /** A box put on a page at ``at`` (x, y in the page's own pixels): moved
   *  left or up (opening above the point) when it would cross the edge of
   *  what can be seen, so its buttons are never under the panel or a bar. */
  _keepInColumn(box, at) {
    const col = this.#column(), r = box.getBoundingClientRect();
    let x = at.x, y = at.y;
    if (r.right > col.right) x -= Math.min(r.right - col.right, r.left - col.left);
    if (r.left < col.left) x += col.left - r.left;
    if (r.bottom > col.bottom) y = r.top - r.height - 12 >= col.top ? at.y - r.height - 12 : at.y - (r.bottom - col.bottom);
    box.style.left = `${x}px`;
    box.style.top = `${y}px`;
  }
  /** The text-look bar over a box being edited: below it when there's no
   *  room above, shifted left when it would run under the panel. */
  _keepBarInColumn(bar) {
    if (!bar.isConnected) return;
    const col = this.#column();
    let r = bar.getBoundingClientRect();
    if (r.top < col.top) { bar.classList.add("below"); r = bar.getBoundingClientRect(); }
    if (r.right > col.right) bar.style.left = `${-Math.min(r.right - col.right, r.left - col.left)}px`;
  }

  /** The drawn part of a canvas as a PNG with a transparent background. */
  async #trim(canvas) {
    const { width, height } = canvas;
    const data = canvas.getContext("2d").getImageData(0, 0, width, height).data;
    let x0 = width, y0 = height, x1 = -1, y1 = -1;
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      if (data[(y * width + x) * 4 + 3] > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    }
    const m = 6, out = document.createElement("canvas");
    x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m); x1 = Math.min(width - 1, x1 + m); y1 = Math.min(height - 1, y1 + m);
    out.width = Math.max(1, x1 - x0 + 1); out.height = Math.max(1, y1 - y0 + 1);
    out.getContext("2d").drawImage(canvas, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
    return new Promise((resolve) => out.toBlob(resolve, "image/png"));
  }
};
