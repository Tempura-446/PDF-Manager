/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Comparing drawing revisions: another version of the document drawn over
// the shown one, page by page. What both have comes out black (or faded,
// "changes only"); what only the other version has, red; what only this
// one has, blue. A moved wall shows as a red line and a blue one.
//
//   const overlay = new Overlay(openDocument, destroyTask, DOC_OPTIONS);
//   await overlay.open({ data } or { url, httpHeaders }, password);
//   await overlay.draw(pageView, pageNumber);   // after the page is drawn
//   overlay.clear(pageView); await overlay.close();

const DARK = 40;  // how dark (0-255) a pixel must be to count as drawn

export class Overlay {
  constructor(openDocument, destroyTask, options) {
    this.openDocument = openDocument;
    this.destroyTask = destroyTask;
    this.options = options;
    this.task = null;
    this.doc = null;
    this.mode = "both";      // "both", "changes", "this", "other"
    this.offset = 0;         // the other version's page = this page + offset
    this.drawing = new WeakMap();  // page view -> the drawing in progress (a newer one supersedes it)
  }

  /** Open the other version: ``source`` is {data} or {url, httpHeaders};
   *  ``askPassword(wrong)`` gives its password (null: cancelled). */
  async open(source, password = null, askPassword = null) {
    await this.close();
    const task = await this.openDocument({ ...this.options, ...source, password: password || undefined });
    if (askPassword) {
      task.onPassword = async (update, reason) => {
        const pw = await askPassword(reason === 2);  // 2: the password given was wrong
        if (pw === null) this.destroyTask(task); else update(pw);
      };
    }
    this.task = task;
    this.doc = await task.promise;
    return this.doc;
  }

  async close() {
    const task = this.task;
    this.task = null;
    this.doc = null;
    if (task) await this.destroyTask(task);
  }

  /** The other version's page shown with page ``n`` of this one, or 0. */
  otherPage(n) {
    const m = n + this.offset;
    return this.doc && m >= 1 && m <= this.doc.numPages ? m : 0;
  }

  /** Draw the comparison over ``view`` (a pdf.js page view, already drawn). */
  async draw(view, n) {
    const page = view?.canvas || view?.div?.querySelector("canvas");
    if (!this.doc || !page || !page.width) return;
    const ticket = {};
    this.drawing.set(view, ticket);
    const m = this.otherPage(n);
    let mark = view.div.querySelector(":scope .rd-overlay-note");
    if (!m) {
      this.clear(view);
      mark = document.createElement("div");
      mark.className = "rd-overlay-note";
      mark.textContent = "The other version has no page here";
      view.div.append(mark);
      return;
    }
    mark?.remove();
    const other = await this.doc.getPage(m);
    if (this.drawing.get(view) !== ticket) return;
    // The other page drawn at the same pixel size as this one (scaled to fit
    // if its sheet is another size).
    // Its own turn, plus the turn the viewer gives every page.
    const turned = ((view.viewport.rotation - (view.pdfPage?.rotate || 0)) % 360 + 360) % 360;
    const rotation = (other.rotate + turned) % 360;
    const base = other.getViewport({ scale: 1, rotation });
    const scale = Math.min(page.width / base.width, page.height / base.height);
    const canvas = document.createElement("canvas");
    canvas.width = page.width;
    canvas.height = page.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await other.render({ canvasContext: ctx, viewport: other.getViewport({ scale, rotation }), annotationMode: 0 }).promise;
    if (this.drawing.get(view) !== ticket || !page.width) return;
    const mine = page.getContext("2d", { willReadFrequently: true }).getImageData(0, 0, page.width, page.height).data;
    const out = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const theirs = out.data;
    const mode = this.mode;
    for (let i = 0; i < theirs.length; i += 4) {
      const a = 255 - (mine[i] * 0.3 + mine[i + 1] * 0.59 + mine[i + 2] * 0.11);  // how dark: this version
      const b = 255 - (theirs[i] * 0.3 + theirs[i + 1] * 0.59 + theirs[i + 2] * 0.11);  // the other version
      let r, g, bl;
      if (mode === "this") { r = g = bl = 255 - a; }
      else if (mode === "other") { r = g = bl = 255 - b; }
      else if (mode === "changes" && a > DARK && b > DARK) { r = g = bl = 225; }  // in both: faded
      else { r = 255 - a; g = 255 - Math.max(a, b); bl = 255 - b; }  // other only: red; this only: blue; both: black
      theirs[i] = r; theirs[i + 1] = g; theirs[i + 2] = bl; theirs[i + 3] = 255;
    }
    ctx.putImageData(out, 0, 0);
    canvas.className = "rd-overlay";
    this.clear(view, false);
    page.after(canvas);
  }

  clear(view, notes = true) {
    view?.div?.querySelectorAll(notes ? ".rd-overlay, .rd-overlay-note" : ".rd-overlay").forEach((n) => n.remove());
  }
}
