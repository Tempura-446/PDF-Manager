// The reader's text editor box (Edit text): finding the line or paragraph
// clicked, its font, size and colour, the box to retype it in, the font
// picker, and what the server says about the font it used. A part of Reader
// (see TextEditing below); the edit itself is textedit.py's, on the server.

import { el, pdfjs, store, svg } from "./readerkit.js";

function middle(node) {
  const r = node.getBoundingClientRect();
  return [r.left + r.width / 2, r.top + r.height / 2];
}

function lineText(line) {
  let text = "";
  line.spans.forEach((it, i) => {
    const prev = line.spans[i - 1];
    if (prev && !/\s$/.test(text) && !/^\s/.test(it.s.textContent) && it.r.left - prev.r.right > line.h * 0.12) text += " ";
    text += it.s.textContent;
  });
  return text.replace(/\s+/g, " ").trim();
}

/** Where the baseline sits in a line box of ``lineHeight`` (px from its top). */
function baselineIn(look, lineHeight) {
  const probe = el("div", {}, "Hg", el("span"));
  Object.assign(probe.style, { position: "absolute", visibility: "hidden", left: "-9999px", top: "0", whiteSpace: "pre",
    fontFamily: look.family, fontSize: `${look.size}px`, fontWeight: look.weight || "", fontStyle: look.style || "",
    lineHeight: `${lineHeight}px` });
  Object.assign(probe.lastChild.style, { display: "inline-block", width: "0", height: "0", verticalAlign: "baseline" });
  document.body.append(probe);
  const at = probe.lastChild.offsetTop;
  probe.remove();
  return at;
}

/** "ABCDEF+TimesNewRomanPS-BoldMT" → "Times New Roman". */
function familyOf(name) {
  let family = String(name || "").replace(/^[A-Z]{6}\+/, "").split(/[-,]/)[0];
  family = family.replace(/(PS)?MT$|PS$/, "").replace(/([a-z])([A-Z])/g, "$1 $2");
  return family.replace(/\s+(Bold|Italic|Oblique|Regular|BoldItalic)$/i, "").trim();
}

function familyKey(name) { return String(name || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }

/** A CSS font-family for a family picked in the list. */
function cssFamily(name, generic) {
  const standard = { helvetica: "Helvetica, Arial, sans-serif", times: '"Times New Roman", Times, serif',
    courier: '"Courier New", Courier, monospace' }[familyKey(name)];
  return standard || `"${name.replace(/["\\]/g, "")}", ${generic}`;
}

function hexColour(css) {
  const m = String(css).match(/\d+(\.\d+)?/g);
  if (/^#[0-9a-f]{6}$/i.test(css)) return css.toLowerCase();
  if (!m || m.length < 3) return "#000000";
  return "#" + m.slice(0, 3).map((v) => Math.max(0, Math.min(255, Math.round(+v))).toString(16).padStart(2, "0")).join("");
}

function rgb01(hex) {
  return [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) / 255 * 1000) / 1000);
}

/** Reader's text editor box, as a class it extends (see Measuring in
 *  measuring.js): with Edit text on, a click on a line opens a box to retype
 *  it (Shift+click: the paragraph), in the document's own font and colour
 *  where it can; a click where there's no text adds new words. The change
 *  itself goes to the server through the reader (_applyTextEdit). */
export const TextEditing = (Base) => class extends Base {
  // ---- changing words on the page ----------------------------------------------------
  /** Edit text tool: a click on a line of text opens a box to retype it. */
  _textClick(e) {
    if (!this.textEditMode || e.button !== 0) return;
    if (e.target.closest?.(".rd-textedit-box")) return;  // inside the open editor
    const pageEl = e.target.closest?.(".page");
    const pageNumber = Number(pageEl?.dataset.pageNumber);
    if (!pageNumber || window.getSelection()?.toString().length > 1) return;
    const span = e.target.closest?.(".textLayer span");
    e.preventDefault();
    e.stopPropagation();
    // The words that show at the click (not old ones covered by a change).
    const line = span && !span.classList.contains("markedContent") && span.textContent.trim()
      ? this.#lineOf(span, pageEl, e.clientX, e.clientY) : this.#lineOf(null, pageEl, e.clientX, e.clientY);
    if (line) {
      const at = line.spans.find((it) => it.r.left <= e.clientX && e.clientX <= it.r.right) || line.spans[0];
      this._openTextEditor(at.s, pageEl, pageNumber, e.shiftKey ? "paragraph" : "line");
    } else {
      this._openNewText(e, pageEl, pageNumber);  // an empty spot: new words there
    }
  }

  /** The lines of text on a page as drawn: [{spans, left, top, right, bottom, h}],
   *  top to bottom. Words next to each other share a line; a far-off column doesn't. */
  /** The page's words that show: [{s: span, r: its box}]. A change covers
   *  the old words and writes them again later on the page; the old ones
   *  are still in the file (and in pdf.js's text) under the new ones. */
  #shownSpans(pageEl) {
    const all = [...pageEl.querySelectorAll(".textLayer span")]
      .filter((s) => !s.classList.contains("markedContent") && s.textContent.trim())
      .map((s) => ({ s, r: s.getBoundingClientRect() }));
    if (all.length > 4000) return all;  // a drawing full of labels: take them all
    return all.filter((it, i) => {
      const area = it.r.width * it.r.height;
      if (!area) return false;
      let under = 0;
      for (let j = i + 1; j < all.length; j++) {
        const o = all[j].r;
        const w = Math.min(it.r.right, o.right) - Math.max(it.r.left, o.left);
        const h = Math.min(it.r.bottom, o.bottom) - Math.max(it.r.top, o.top);
        if (w > 0 && h > it.r.height * 0.5) under += w * h;
      }
      return under < area * 0.5;
    });
  }

  /** The words on the line at a span (or a point): next to each other,
   *  without the gap of another column. Or null. */
  #lineOf(span, pageEl, x, y) {
    const lines = this.#pageLines(pageEl, 1.2);
    return lines.find((l) => l.spans.some((it) => it.s === span))
      || lines.find((l) => x >= l.left - 2 && x <= l.right + 2 && y >= l.top - 1 && y <= l.bottom + 1) || null;
  }

  #pageLines(pageEl, apart = 2.5) {
    const items = this.#shownSpans(pageEl)
      .sort((a, b) => (a.r.top + a.r.bottom) / 2 - (b.r.top + b.r.bottom) / 2 || a.r.left - b.r.left);
    const lines = [];
    for (const it of items) {
      const h = it.r.height, cy = (it.r.top + it.r.bottom) / 2;
      const line = lines.find((l) => Math.abs((l.top + l.bottom) / 2 - cy) < Math.min(l.h, h) * 0.5
        && it.r.left - l.right < Math.max(l.h, h) * apart && l.left - it.r.right < Math.max(l.h, h) * apart);
      if (line) {
        line.spans.push(it);
        Object.assign(line, { left: Math.min(line.left, it.r.left), right: Math.max(line.right, it.r.right),
          top: Math.min(line.top, it.r.top), bottom: Math.max(line.bottom, it.r.bottom), h: Math.max(line.h, h) });
      } else {
        lines.push({ spans: [it], left: it.r.left, right: it.r.right, top: it.r.top, bottom: it.r.bottom, h });
      }
    }
    for (const l of lines) l.spans.sort((a, b) => a.r.left - b.r.left);
    return lines.sort((a, b) => a.top - b.top);
  }



  /** The paragraph around ``span``: the lines above and below it set the same way. */
  #paragraphOf(span, pageEl) {
    const lines = this.#pageLines(pageEl);
    let at = lines.findIndex((l) => l.spans.some((it) => it.s === span));
    if (at < 0) return null;
    // Lines of one paragraph: close below each other, in one column, and in
    // the same type (a heading or a differently set paragraph is another).
    const type = (l) => {
      const st = l.spans[0].s.style;
      return [parseFloat(st.getPropertyValue("--font-height")) || l.h, st.fontFamily];
    };
    const close = (a, b) => {
      const gap = b.top - a.bottom, h = Math.min(a.h, b.h);
      const [fa, ka] = type(a), [fb, kb] = type(b);
      return gap > -h * 0.3 && gap < h * 1.1 && Math.abs(a.h - b.h) < h * 0.35
        && Math.abs(fa - fb) <= Math.max(fa, fb) * 0.08 && ka === kb
        && b.left < a.right && b.right > a.left && Math.abs(b.left - a.left) < h * 3;
    };
    let first = at, last = at;
    // A paragraph is a column of lines: only neighbours in that column count.
    const column = (l) => l.left < lines[at].right && l.right > lines[at].left;
    const above = (i) => { for (let j = i - 1; j >= 0; j--) if (column(lines[j])) return j; return -1; };
    const below = (i) => { for (let j = i + 1; j < lines.length; j++) if (column(lines[j])) return j; return -1; };
    for (let j = above(first); j >= 0 && close(lines[j], lines[first]); j = above(first)) first = j;
    for (let j = below(last); j >= 0 && close(lines[last], lines[j]); j = below(last)) last = j;
    const chosen = lines.slice(first, last + 1).filter((l, i, all) => i === 0 || i === all.length - 1 || column(l));
    let text = "";
    for (const line of chosen) {
      const words = lineText(line);
      if (/[a-z]-$/.test(text) && /^[a-z]/.test(words)) text = text.slice(0, -1) + words;  // a word broken over two lines
      else text += (text ? " " : "") + words;
    }
    return {
      text, left: Math.min(...chosen.map((l) => l.left)), right: Math.max(...chosen.map((l) => l.right)),
      top: chosen[0].top, bottom: chosen.at(-1).bottom, h: lines[at].h, lines: chosen.length,
      first: chosen[0].spans[0].s,  // its first words, for the look
      lead: chosen.length > 1 ? (chosen.at(-1).top - chosen[0].top) / (chosen.length - 1) : lines[at].h * 1.2,
    };
  }

  /** A box on the page for typing: [wrapper, input or textarea]. */
  /** How words of the page look on screen, to type over them in place:
   *  {family, size, left, baseline (screen px), color, background}, from
   *  the text pdf.js read (its font is the document's own), or null. */
  async #textLook(span, pageNumber) {
    try {
      const view = this.pdfViewer.getPageView(pageNumber - 1);
      if (!view?.pdfPage || view.viewport.rotation % 360 !== 0) return null;
      this.textContents ||= new WeakMap();
      let content = this.textContents.get(view.pdfPage);
      if (!content) {
        content = await view.pdfPage.getTextContent();
        this.textContents.set(view.pdfPage, content);
      }
      const layer = span.closest(".textLayer")?.getBoundingClientRect();
      if (!layer || !span.isConnected) return null;
      const r = span.getBoundingClientRect();
      const [a, b, c, d, e, f] = view.viewport.transform;
      let best = null, far = Infinity;
      for (const item of content.items) {
        if (!item.str || item.str !== span.textContent) continue;
        const [ia, ib, ic, id, ie, iff] = item.transform;
        const tx = [a * ia + c * ib, b * ia + d * ib, a * ic + c * id, b * ic + d * id, a * ie + c * iff + e, b * ie + d * iff + f];
        const size = Math.hypot(tx[2], tx[3]);
        if (Math.abs(tx[1]) > size * 0.01 || Math.abs(tx[2]) > size * 0.01) continue;  // not level
        const dist = Math.abs(layer.left + tx[4] - r.left) + Math.abs(layer.top + tx[5] - (r.top + r.height * 0.8));
        if (dist < far) { far = dist; best = { item, tx, size }; }
      }
      if (!best || far > best.size) return null;
      const { item, tx, size } = best;
      const generic = content.styles[item.fontName]?.fontFamily || "sans-serif";
      let real = null;  // the font's own name, for the copy installed on this computer
      try { if (view.pdfPage.commonObjs.has(item.fontName)) real = view.pdfPage.commonObjs.get(item.fontName); } catch (err) { /* not read yet */ }
      const name = String(real?.name || "").replace(/^[A-Z]{6}\+/, "");
      const bold = !!(real?.bold || real?.black) || /bold|black|heavy|semibold|demi/i.test(name);
      const italic = !!real?.italic || /italic|oblique/i.test(name);
      const look = { size, left: layer.left + tx[4], baseline: layer.top + tx[5], width: r.width, fontName: name,
        weight: bold ? "bold" : "", style: italic ? "italic" : "", ...this.#pageColours(span.closest(".page"), r) };
      // The document's font as pdf.js loaded it shows the words just as on the
      // page, unless it mixed up its letters (fonts cut down for a PDF can
      // map a letter to the wrong shape); so it's checked against the page,
      // then the same font installed here, then a plain one of its kind.
      const candidates = [];
      if ([...document.fonts].some((ff) => ff.family.replace(/["']/g, "") === item.fontName && ff.status === "loaded")) {
        candidates.push({ family: `"${item.fontName}", ${generic}`, weight: "", style: "" });
      }
      const base = name.replace(/[-,]?(bold|italic|oblique|regular|roman|book|semibold|black|medium|light|mt|ps)+$/gi, "").replace(/(ps)?mt$/i, "");
      for (const n of new Set([base.replace(/([a-z])([A-Z])/g, "$1 $2"), base, name.replace(/[-,]/g, " ")])) {
        if (n && /^[\w .]+$/.test(n)) candidates.push({ family: `"${n}", ${generic}`, weight: look.weight, style: look.style });
      }
      const pageEl = span.closest(".page");
      for (const c of candidates) {
        if (this.#inkMatches(pageEl, { ...look, ...c }, span.textContent, r)) return { ...look, ...c };
      }
      return { ...look, family: generic };
    } catch (err) {
      return null;  // the plain box will do
    }
  }

  /** The paper and ink colours around a box on the page as drawn. */
  #pageColours(pageEl, r) {
    const out = { background: "#fff", color: "#111" };
    try {
      const canvas = pageEl?.querySelector("canvas");
      if (!canvas) return out;
      const cr = canvas.getBoundingClientRect(), k = canvas.width / cr.width;
      const x0 = Math.max(0, Math.floor((r.left - cr.left) * k) - 3), y0 = Math.max(0, Math.floor((r.top - cr.top) * k) - 3);
      const w = Math.min(canvas.width - x0, Math.ceil(r.width * k) + 6), h = Math.min(canvas.height - y0, Math.ceil(r.height * k) + 6);
      if (w < 2 || h < 2) return out;
      const px = canvas.getContext("2d").getImageData(x0, y0, w, h).data;
      const edge = [];
      for (let x = 0; x < w; x++) edge.push((x) * 4, ((h - 1) * w + x) * 4);
      for (let y = 0; y < h; y++) edge.push(y * w * 4, (y * w + w - 1) * 4);
      const mid = (ch) => { const v = edge.map((i) => px[i + ch]).sort((p, q) => p - q); return v[v.length >> 1]; };
      const bg = [mid(0), mid(1), mid(2)];
      let ink = null, most = 60;
      for (let i = 0; i < px.length; i += 4) {
        const dist = Math.abs(px[i] - bg[0]) + Math.abs(px[i + 1] - bg[1]) + Math.abs(px[i + 2] - bg[2]);
        if (dist > most) { most = dist; ink = [px[i], px[i + 1], px[i + 2]]; }
      }
      out.background = `rgb(${bg.join(" ")})`;
      if (ink) out.color = `rgb(${ink.join(" ")})`;
    } catch (err) { /* a canvas that can't be read: black on white */ }
    return out;
  }

  /** The spacing that makes ``text`` in a font as wide as on the page:
   *  {letter, word} in px, or {}. */
  #fitSpacing(text, look, width) {
    const natural = this.#textWidth(text, look), spaces = text.split(" ").length - 1;
    const diff = width - natural;
    if (Math.abs(diff) <= 0.5 || text.length < 2) return {};
    return diff > 0 && spaces ? { word: Math.min(diff / spaces, look.size * 1.5) }
      : { letter: Math.max(-look.size * 0.25, Math.min(diff / (text.length - 1), look.size * 0.25)) };
  }

  /** Whether ``text`` drawn in the look's font lands on the page's own ink
   *  in the box ``r`` (same letters, same shapes). */
  #inkMatches(pageEl, look, text, r) {
    try {
      const canvas = pageEl?.querySelector("canvas");
      if (!canvas || !text.trim()) return false;
      const cr = canvas.getBoundingClientRect(), k = canvas.width / cr.width;
      const x0 = Math.max(0, Math.floor((r.left - cr.left) * k)), y0 = Math.max(0, Math.floor((r.top - cr.top) * k));
      const w = Math.min(canvas.width - x0, Math.ceil(r.width * k) + 2), h = Math.min(canvas.height - y0, Math.ceil(r.height * k) + 2);
      if (w < 4 || h < 4) return false;
      const page = canvas.getContext("2d").getImageData(x0, y0, w, h).data;
      const off = document.createElement("canvas");
      off.width = w; off.height = h;
      const c = off.getContext("2d", { willReadFrequently: true });
      const spacing = this.#fitSpacing(text, look, r.width);
      c.font = `${look.style || ""} ${look.weight || ""} ${look.size * k}px ${look.family}`;
      if ("letterSpacing" in c) c.letterSpacing = `${(spacing.letter || 0) * k}px`;
      if ("wordSpacing" in c) c.wordSpacing = `${(spacing.word || 0) * k}px`;
      c.fillText(text, (look.left - cr.left) * k - x0, (look.baseline - cr.top) * k - y0);
      const ours = c.getImageData(0, 0, w, h).data;
      const lum = (d, i) => d[i] * 0.3 + d[i + 1] * 0.59 + d[i + 2] * 0.11;
      let bg = 0;
      for (let x = 0; x < w; x++) bg += lum(page, x * 4) + lum(page, ((h - 1) * w + x) * 4);
      bg /= 2 * w;
      const inkA = new Uint8Array(w * h), inkB = new Uint8Array(w * h);
      for (let i = 0; i < w * h; i++) {
        inkA[i] = Math.abs(lum(page, i * 4) - bg) > 70 ? 1 : 0;
        inkB[i] = ours[i * 4 + 3] > 110 ? 1 : 0;
      }
      const near = (ink, i) => {  // ink within a pixel
        const x = i % w, y = (i / w) | 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx, yy = y + dy;
          if (xx >= 0 && yy >= 0 && xx < w && yy < h && ink[yy * w + xx]) return true;
        }
        return false;
      };
      let a = 0, b = 0, ab = 0, ba = 0;
      for (let i = 0; i < w * h; i++) {
        if (inkA[i]) { a++; if (near(inkB, i)) ab++; }
        if (inkB[i]) { b++; if (near(inkA, i)) ba++; }
      }
      return a > 0 && b > 0 && ab / a > 0.96 && ba / b > 0.96;
    } catch (err) {
      return false;
    }
  }


  /** How wide ``text`` is in a font, in px. */
  #textWidth(text, look) {
    this.measureCtx ||= document.createElement("canvas").getContext("2d");
    this.measureCtx.font = `${look.style || ""} ${look.weight || ""} ${look.size}px ${look.family}`;
    return this.measureCtx.measureText(text).width;
  }

  /** A box on the page for typing: [wrapper, input or textarea]. With a
   *  ``look``, the words in it sit exactly on the page's own: same font,
   *  size, spacing, colour and baseline, so nothing moves as you type. */
  #editorBox(pageEl, { left, top, width, height, fontSize, fontFamily, multiline, value, label, hint, look, lineHeight }) {
    const page = pageEl.getBoundingClientRect();
    const field = multiline
      ? el("textarea", { class: "rd-textedit", spellcheck: "true", "aria-label": label })
      : el("input", { type: "text", class: "rd-textedit", spellcheck: "true", "aria-label": label });
    field.value = value;
    // Double-clicking a word selects the space after it too (Chromium on
    // Windows), so typing a replacement swallowed the space: select the word.
    field.addEventListener("dblclick", () => {
      let { selectionStart: start, selectionEnd: end } = field;
      while (end > start && /\s/.test(field.value[end - 1])) end--;
      if (end !== field.selectionEnd) field.setSelectionRange(start, end);
    });
    const box = el("div", { class: "rd-textedit-box" }, field, el("div", { class: "rd-textedit-hint" }, ...hint));
    if (look) {
      const lh = lineHeight || Math.round(look.size * 1.3 * 100) / 100;
      const spacing = look.spacing || {};
      field.classList.add("rd-textedit-fit");
      Object.assign(field.style, { fontFamily: look.family, fontSize: `${look.size}px`, lineHeight: `${lh}px`,
        fontWeight: look.weight || "", fontStyle: look.style || "",
        color: look.color, background: look.background, width: `${width}px`, height: `${multiline ? height : lh}px`,
        letterSpacing: spacing.letter ? `${spacing.letter}px` : "", wordSpacing: spacing.word ? `${spacing.word}px` : "" });
      left = look.left;
      top = look.baseline - baselineIn(look, lh);
      Object.assign(box.style, { left: `${left - page.left - pageEl.clientLeft}px`, top: `${top - page.top - pageEl.clientTop}px` });
      if (!multiline) {  // grows with the words
        const grow = () => {
          const t = field.value;
          const w = this.#textWidth(t, look) + (spacing.letter || 0) * t.length
            + (spacing.word || 0) * (t.split(" ").length - 1) + look.size;
          field.style.width = `${Math.max(width, Math.ceil(w))}px`;
        };
        field.addEventListener("input", grow);
        grow();
      }
      return [box, field];
    }
    Object.assign(field.style, { width: `${width}px`, height: `${height}px`, fontSize: `${fontSize}px`, fontFamily: fontFamily || "" });
    Object.assign(box.style, { left: `${left - page.left - pageEl.clientLeft - 3}px`, top: `${top - page.top - pageEl.clientTop - 2}px` });
    return [box, field];
  }

  async _openTextEditor(span, pageEl, pageNumber, mode = "line") {
    this.#closeTextEditor(false);
    const ticket = (this.editTicket = (this.editTicket || 0) + 1);
    const viewport = this.pdfViewer.getPageView(pageNumber - 1).viewport;
    const sideways = viewport.rotation % 180 !== 0;
    const paragraph = mode === "paragraph" && !sideways ? this.#paragraphOf(span, pageEl) : null;
    const first = paragraph ? paragraph.first
      : (!sideways && this.#lineOf(span, pageEl, ...middle(span))?.spans[0].s) || span;
    const look = await this.#textLook(first, pageNumber);
    if (ticket !== this.editTicket || !span.isConnected || !this.textEditMode) return;  // clicked elsewhere meanwhile
    const layer = pageEl.querySelector(".textLayer").getBoundingClientRect();
    const r = span.getBoundingClientRect();
    // A line is all its words, however pdf.js split them up.
    const line = !paragraph && !sideways ? this.#lineOf(span, pageEl, r.left + r.width / 2, r.top + r.height / 2) : null;
    const area = paragraph || line || { left: r.left, right: r.right, top: r.top, bottom: r.bottom, h: sideways ? r.width : r.height };
    const original = paragraph ? paragraph.text : line ? lineText(line) : span.textContent;
    // With the view turned, the line runs up or down the screen: size the box
    // from the line's own length and height.
    const lineW = sideways ? r.height : area.right - area.left, lineH = area.h;
    // The page's spacing, so the typed words line up with the page's to the end.
    if (look && !paragraph) look.spacing = this.#fitSpacing(original, look, area.right - look.left);
    const toggle = el("button", { type: "button", class: "rd-textedit-mode", title: paragraph ? "Change this line only" : "Rewrite the whole paragraph (or Shift+click a line)" },
      paragraph ? "One line" : "¶ Whole paragraph");
    toggle.disabled = sideways;
    const lead = paragraph ? paragraph.lead : null;
    const [box, field] = this.#editorBox(pageEl, {
      left: area.left, top: area.top, multiline: !!paragraph, value: original, look, lineHeight: lead,
      width: look ? (paragraph ? lineW + look.size * 0.6 : Math.max(lineW + look.size, 80)) : Math.max(lineW + (paragraph ? 16 : 40), 80),
      height: paragraph ? (look ? lead * (paragraph.lines + 1) : area.bottom - area.top + lineH * 1.5) : lineH + 4,
      fontSize: Math.max(8, lineH * 0.8), fontFamily: getComputedStyle(span).fontFamily,
      label: paragraph ? "New paragraph (Ctrl+Enter to change, Esc to cancel)" : "New text (Enter to change, Esc to cancel)",
      hint: [el("span", {}, paragraph ? "Ctrl+Enter to change · Esc to cancel" : "Enter to change · Esc to cancel"), toggle],
    });
    // The words' box in PDF points (measured from the page as drawn).
    const [ax, ay] = viewport.convertToPdfPoint(area.left - layer.left, area.top - layer.top);
    const [bx, by] = viewport.convertToPdfPoint(area.right - layer.left, area.bottom - layer.top);
    const rect = [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];
    toggle.addEventListener("pointerdown", (e) => e.preventDefault());  // keeps the field's focus
    toggle.addEventListener("click", () => {
      this.#closeTextEditor(false);
      this._openTextEditor(span, pageEl, pageNumber, paragraph ? "line" : "paragraph");
    });
    // The words as shown go along, so only what was changed is drawn again.
    const picked = this.#fontPicker(box, field, look, viewport, { paragraph: !!paragraph, size: lineH });
    this.#startEditing(box, field, pageEl, original, (text) =>
      this._applyTextEdit({ page: pageNumber, rect, text, ...(paragraph ? { paragraph: true } : { old: original }),
        ...picked.choices() }), picked.changed);
  }

  /** New words where the page was clicked; they take the look of the nearest text. */
  async _openNewText(e, pageEl, pageNumber) {
    this.#closeTextEditor(true);
    const ticket = (this.editTicket = (this.editTicket || 0) + 1);
    const at = { clientX: e.clientX, clientY: e.clientY };
    const viewport = this.pdfViewer.getPageView(pageNumber - 1).viewport;
    let near = null, best = Infinity;
    for (const line of this.#pageLines(pageEl)) {
      const d = Math.hypot(Math.max(line.left - at.clientX, 0, at.clientX - line.right), Math.max(line.top - at.clientY, 0, at.clientY - line.bottom) * 2);
      if (d < best) { best = d; near = line; }
    }
    const look = near ? await this.#textLook(near.spans[0].s, pageNumber) : null;
    if (ticket !== this.editTicket || !pageEl.isConnected || !this.textEditMode) return;
    const layer = pageEl.querySelector(".textLayer")?.getBoundingClientRect() || pageEl.getBoundingClientRect();
    if (look) {  // the new words' top left at the click, as they'll be drawn
      Object.assign(look, { left: at.clientX, baseline: at.clientY + look.size * 0.8 },
        this.#pageColours(pageEl, { left: at.clientX, top: at.clientY, width: 40, height: look.size }));
      if (near) look.color = this.#pageColours(pageEl, near.spans[0].r).color;
    }
    const h = near ? near.h : 16 * viewport.scale;
    const [box, field] = this.#editorBox(pageEl, {
      left: at.clientX, top: at.clientY, width: look ? Math.max(160, look.size * 8) : 240, height: look ? look.size * 1.2 : h + 4,
      fontSize: Math.max(8, h * 0.8), multiline: true, lineHeight: look ? look.size * 1.2 : null,
      fontFamily: near ? getComputedStyle(near.spans[0].s).fontFamily : "", value: "", look,
      label: "New text (Enter to add, Shift+Enter for a new line, Esc to cancel)",
      hint: [el("span", {}, "New text · Enter to add · Shift+Enter new line · Esc to cancel")],
    });
    field.dataset.enter = "apply";  // Enter adds the words; Shift+Enter starts a new line
    field.rows = 1;
    const [x, y] = viewport.convertToPdfPoint(at.clientX - layer.left, at.clientY - layer.top);
    const picked = this.#fontPicker(box, field, look, viewport, { newText: true, size: h });
    this.#startEditing(box, field, pageEl, "", (text) => this._applyTextEdit({ page: pageNumber, at: [x, y], text,
      ...picked.choices() }));
  }

  #startEditing(box, field, pageEl, original, apply, restyled = null) {
    let done = false;
    const finish = (keep) => {
      if (done) return;
      done = true;
      if (this.textEditor?.box === box) this.textEditor = null;
      if (keep && (field.value !== original || restyled?.()) && (original || field.value.trim())) {
        // The typed words stay showing where they go until the page is
        // drawn again with them, so nothing flickers back to the old ones.
        box.classList.add("rd-pending");
        field.readOnly = true;
        field.blur();
        Promise.resolve(apply(field.value)).finally(() => box.remove());
      } else {
        box.remove();
      }
    };
    field.addEventListener("keydown", (e) => {
      e.stopPropagation();
      const multiline = field.tagName === "TEXTAREA";
      if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey || (field.dataset.enter === "apply" && !e.shiftKey))) { e.preventDefault(); finish(true); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    field.addEventListener("blur", () => setTimeout(() => { if (!done && !box.contains(document.activeElement)) finish(true); }));
    pageEl.append(box);
    this.textEditor = { box, finish };
    field.focus();
    field.select();
  }

  #closeTextEditor(keep) { this.textEditor?.finish(keep); }

  // -- text font picker and reflow --
  /** The bar above the text editor to choose how the words look: font,
   *  size, bold, italic, colour and (for more than a word) alignment. The
   *  field shows the choice as it's made. Returns {choices(): what changed,
   *  as the server takes it, changed(): whether anything did}. */
  #fontPicker(box, field, look, viewport, { paragraph = false, newText = false, size = 16 } = {}) {
    const scale = viewport.scale || 1;
    const start = {
      font: "", size: Math.round(((look?.size || size * 0.8) / scale) * 2) / 2,
      bold: look?.weight === "bold", italic: look?.style === "italic",
      color: hexColour(look?.color || "#000000"), align: "",
    };
    const now = { ...start };
    const own = familyOf(look?.fontName || "");
    const fontSel = el("select", { class: "rd-tf-font", title: "Font", "aria-label": "Font" },
      el("option", { value: "" }, own ? `${own} (as is)` : "Same font"));
    const sizeIn = el("input", { class: "rd-tf-size", type: "number", min: "1", max: "500", step: "0.5", value: String(start.size),
      title: "Size (points)", "aria-label": "Font size in points" });
    const toggle = (label, title, key) => el("button", { type: "button", class: `rd-tf-btn rd-tf-${key}`, title,
      "aria-label": title, "aria-pressed": String(now[key]) }, label);
    const boldBtn = toggle("B", "Bold (Ctrl+B)", "bold"), italicBtn = toggle("I", "Italic (Ctrl+I)", "italic");
    const colorIn = el("input", { class: "rd-tf-color", type: "color", value: start.color, title: "Colour", "aria-label": "Text colour" });
    const aligns = [["left", "Align left", "M4 6h16M4 10h10M4 14h16M4 18h10"], ["center", "Centre", "M4 6h16M7 10h10M4 14h16M7 18h10"],
      ["right", "Align right", "M4 6h16M10 10h10M4 14h16M10 18h10"], ["justify", "Justify", "M4 6h16M4 10h16M4 14h16M4 18h16"]]
      .map(([value, title, d]) => {
        const b = el("button", { type: "button", class: "rd-tf-btn rd-tf-align", title, "aria-label": title, "aria-pressed": "false",
          html: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>` });
        b.dataset.align = value;
        return b;
      });
    const alignGroup = el("span", { class: "rd-tf-group", role: "group", "aria-label": "Alignment" }, ...aligns);
    aligns[3].hidden = !paragraph && !newText;  // one line: set within its old width; justifying needs more lines
    const bar = el("div", { class: "rd-tf-bar", role: "toolbar", "aria-label": "Text look" },
      fontSel, sizeIn, el("span", { class: "rd-tf-group" }, boldBtn, italicBtn), colorIn, alignGroup);
    box.append(bar);
    requestAnimationFrame(() => this._keepBarInColumn(bar));

    const generic = /serif|times|roman|georgia|garamond|cambria|book|palatino/i.test(own) && !/sans/i.test(own) ? "serif"
      : /mono|courier|consol/i.test(own) ? "monospace" : "sans-serif";
    const original = look ? { family: look.family, size: look.size, weight: look.weight, style: look.style, color: look.color } : null;
    const lineHeight = parseFloat(field.style.lineHeight) || 0;
    const show = () => {
      const k = now.size / start.size;
      const family = now.font ? cssFamily(now.font, generic) : original?.family || "";
      if (look) Object.assign(look, { family, size: original.size * k, weight: now.bold ? "bold" : "", style: now.italic ? "italic" : "" });
      Object.assign(field.style, {
        fontFamily: family, fontWeight: now.bold ? "bold" : "normal", fontStyle: now.italic ? "italic" : "normal",
        color: now.color !== start.color || !original ? now.color : original.color, textAlign: now.align === "justify" ? "justify" : now.align || "",
      });
      if (look) field.style.fontSize = `${look.size}px`;
      else field.style.fontSize = `${Math.max(8, now.size * scale)}px`;
      if (lineHeight) field.style.lineHeight = `${lineHeight * k}px`;
      if (now.font || now.bold !== start.bold || now.italic !== start.italic) {
        field.style.letterSpacing = field.style.wordSpacing = "";  // the page's spacing was for its own font
      }
      if (paragraph && lineHeight) field.style.height = `${Math.max(parseFloat(field.style.height) || 0, lineHeight * k * 2)}px`;
      fit();
    };
    // New words: the box grows with the longest line and the number of lines.
    const fit = () => {
      if (!newText) return field.dispatchEvent(new Event("input"));
      const lines = field.value.split("\n");
      const m = look || { family: getComputedStyle(field).fontFamily, size: parseFloat(field.style.fontSize) || 16 };
      const w = Math.max(...lines.map((t) => this.#textWidth(t, m)), 0) + m.size;
      const lh = parseFloat(field.style.lineHeight) || m.size * 1.25;
      Object.assign(field.style, { width: `${Math.max(look ? look.size * 4 : 160, Math.ceil(w))}px`, height: `${Math.ceil(lh * lines.length + 2)}px` });
    };
    if (newText) field.addEventListener("input", fit);

    const press = () => {
      boldBtn.setAttribute("aria-pressed", String(now.bold));
      italicBtn.setAttribute("aria-pressed", String(now.italic));
      for (const b of aligns) b.setAttribute("aria-pressed", String(b.dataset.align === now.align));
      show();
    };
    for (const b of [boldBtn, italicBtn, ...aligns]) b.addEventListener("pointerdown", (e) => e.preventDefault());  // the field keeps the focus
    boldBtn.addEventListener("click", () => { now.bold = !now.bold; press(); });
    italicBtn.addEventListener("click", () => { now.italic = !now.italic; press(); });
    for (const b of aligns) b.addEventListener("click", () => { now.align = now.align === b.dataset.align ? "" : b.dataset.align; press(); });
    fontSel.addEventListener("change", () => { now.font = fontSel.value; show(); });
    sizeIn.addEventListener("input", () => {
      const v = parseFloat(sizeIn.value);
      if (v >= 1 && v <= 500) { now.size = v; show(); }
    });
    colorIn.addEventListener("input", () => { now.color = colorIn.value; show(); });
    field.addEventListener("keydown", (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === "b" || key === "i") {
        e.preventDefault();
        now[key === "b" ? "bold" : "italic"] = !now[key === "b" ? "bold" : "italic"];
        press();
      }
    });
    // The bar's own keys: Enter applies, Esc cancels; nothing reaches the reader's shortcuts.
    bar.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") { e.preventDefault(); this.textEditor?.finish(false); }
      if (e.key === "Enter" && e.target.tagName !== "BUTTON") { e.preventDefault(); this.textEditor?.finish(true); }
    });
    // Leaving the bar for the page is like leaving the field: the change is made.
    bar.addEventListener("focusout", () => setTimeout(() => {
      if (box.isConnected && !box.contains(document.activeElement) && this.textEditor?.box === box) this.textEditor.finish(true);
    }));

    this.#fontChoices().then((choices) => {
      if (!choices || !fontSel.isConnected) return;
      const ownKey = familyKey(own);
      const group = (label, names) => {
        const items = (names || []).filter((n) => familyKey(n) !== ownKey);
        if (items.length) fontSel.append(el("optgroup", { label }, ...items.map((n) => el("option", { value: n }, n))));
      };
      group("In this document", choices.document);
      group("On this computer", choices.installed);
      group("More on this computer", choices.more);
      group(choices.pdfa ? "Standard (embedded, PDF/A)" : "Standard", choices.standard);
    });

    const changes = () => {
      const out = {};
      if (now.font) out.font = now.font;
      if (Math.abs(now.size - start.size) >= 0.25) out.size = now.size;
      if (now.bold !== start.bold) out.bold = now.bold;
      if (now.italic !== start.italic) out.italic = now.italic;
      if (now.color !== start.color) out.color = rgb01(now.color);
      if (now.align) out.align = now.align;
      return out;
    };
    return { choices: changes, changed: () => Object.keys(changes()).length > 0 };
  }

  /** The fonts to offer (see /api/text/fonts), asked once per version of the document. */
  async #fontChoices() {
    const tab = this.activeTab;
    if (!tab) return null;
    if (tab.fontChoices?.version !== tab.version) {
      const ask = async () => {
        try {
          const bytes = tab.bytes || await this._currentBytes();
          const res = await this._post("/api/text/fonts", bytes, tab);
          return res.ok ? await res.json() : null;
        } catch (e) {
          return null;  // the editor works without the list: the font as is, size, colour
        }
      };
      tab.fontChoices = { version: tab.version, promise: ask() };
    }
    return tab.fontChoices.promise;
  }

  /** Notes on a change: the font it got when one was picked, a smaller
   *  size, words that ran on into the next column: [sentence]. */
  _fontNotes(used) {
    const notes = [];
    for (const u of used) {
      if (u.asked && u.matched === false) {
        notes.push(u.reason === "pdfa"
          ? `This is a PDF/A file, which needs every font inside it; ${u.asked} isn't, so ${u.font} was used.`
          : `${u.asked} can't show those letters in that style here, so ${u.font} was used.`);
      }
      if (u.size) notes.push(`The text was made a little smaller (${u.size} pt) to fit.`);
      if (u.linked) notes.push("What didn't fit in its column runs on at the top of the next one.");
    }
    return notes;
  }

  /** The last change again, its text made a little smaller to fit the page. */
  async _shrinkToFit(edit) {
    await this._undoTextEdit();
    return this._applyTextEdit({ ...edit, fit: "shrink" });
  }





  // -- end of text font picker and reflow --
};
