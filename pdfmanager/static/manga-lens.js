/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Live "Translate This Page" Manga Lens & Interactive Bubble Editor for the Reader.
// Renders editable translated bubble overlays directly over the active PDF.js page,
// supports hold-Space to peek at original artwork, and burns edited bubbles into the
// PDF via Reader._changeDocument (with full Undo/Redo).

import { el, store } from "./readerkit.js";

const DEFAULT_LANGS = [
  ["en", "English"], ["ja", "Japanese"], ["zh-Hans", "Chinese (Simplified)"],
  ["zh-Hant", "Chinese (Traditional)"], ["ko", "Korean"], ["es", "Spanish"],
  ["fr", "French"], ["de", "German"], ["pt", "Portuguese"], ["ru", "Russian"],
  ["it", "Italian"], ["vi", "Vietnamese"], ["th", "Thai"], ["id", "Indonesian"],
];

export class MangaLens {
  constructor(reader) {
    this.reader = reader;
    this.active = false;
    this.peeking = false;
    this.pageNum = null;
    this.bubbles = [];
    this.selectedId = null;
    this.dstLang = store.get("pdfm-lens-to", "en");
    this.srcLang = store.get("pdfm-lens-from", "ja");
    this.useAi = store.get("pdfm-lens-ai", true) !== false;
    this.bar = null;
    this.layer = null;
    this._onKeyDown = (e) => this.#handleKeyDown(e);
    this._onKeyUp = (e) => this.#handleKeyUp(e);
    this._onPageRendered = (ev) => {
      if (this.active && ev?.pageNumber === this.pageNum) this.#renderOverlay();
    };
  }

  async open({ autoTranslate = true } = {}) {
    if (!this.reader.doc) return;
    if (this.active) {
      if (autoTranslate) await this.translateCurrentPage();
      return;
    }
    this.active = true;
    this.peeking = false;
    this.bubbles = [];
    this.selectedId = null;
    await this.#buildBar();
    document.addEventListener("keydown", this._onKeyDown, true);
    document.addEventListener("keyup", this._onKeyUp, true);
    this.reader.eventBus?.on("pagerendered", this._onPageRendered);
    if (autoTranslate) await this.translateCurrentPage();
  }

  close() {
    if (!this.active) return;
    this.active = false;
    this.peeking = false;
    this.bubbles = [];
    this.selectedId = null;
    this.#clearOverlay();
    if (this.bar) {
      this.bar.remove();
      this.bar = null;
    }
    document.removeEventListener("keydown", this._onKeyDown, true);
    document.removeEventListener("keyup", this._onKeyUp, true);
    this.reader.eventBus?.off("pagerendered", this._onPageRendered);
  }

  async #buildBar() {
    if (this.bar) this.bar.remove();
    let langs = DEFAULT_LANGS;
    try {
      const fetched = await this.reader._translateLanguages?.();
      if (Array.isArray(fetched) && fetched.length) langs = fetched;
    } catch { /* fallback to DEFAULT_LANGS */ }

    const mkSel = (list, def) => {
      const s = el("select", { class: "rd-lens-sel" });
      for (const [code, label] of list) {
        const opt = el("option", { value: code }, label);
        if (code === def) opt.selected = true;
        s.append(opt);
      }
      return s;
    };

    this.fromSel = mkSel([["auto", "Auto"], ...langs], this.srcLang);
    this.toSel = mkSel(langs, this.dstLang);
    this.fromSel.addEventListener("change", () => {
      this.srcLang = this.fromSel.value;
      store.set("pdfm-lens-from", this.srcLang);
    });
    this.toSel.addEventListener("change", () => {
      this.dstLang = this.toSel.value;
      store.set("pdfm-lens-to", this.dstLang);
    });

    this.aiChk = el("input", { type: "checkbox" });
    this.aiChk.checked = this.useAi;
    this.aiChk.addEventListener("change", () => {
      this.useAi = this.aiChk.checked;
      store.set("pdfm-lens-ai", this.useAi);
    });
    const aiLbl = el("label", { class: "rd-lens-chk", title: "Use Manga AI Pack (neural detector, Baberu OCR JA/ZH/EN, LaMa)" },
      this.aiChk, el("span", {}, "Manga AI"));

    this.statusEl = el("span", { class: "rd-lens-status" }, "Ready");
    this.runBtn = el("button", { type: "button", class: "btn sm seal" }, "Translate page");
    this.runBtn.addEventListener("click", () => this.translateCurrentPage());

    this.peekBtn = el("button", { type: "button", class: "btn sm", title: "Hold Space or click to peek at original" }, "Original");
    this.peekBtn.addEventListener("click", () => this.setPeek(!this.peeking));

    this.applyBtn = el("button", { type: "button", class: "btn sm", disabled: true, title: "Burn edited bubbles into this PDF page (Undoable)" }, "Apply to PDF");
    this.applyBtn.addEventListener("click", () => this.applyToPdf());

    const closeBtn = el("button", { type: "button", class: "btn sm", title: "Close Manga Lens (Esc)" }, "×");
    closeBtn.addEventListener("click", () => this.close());

    this.bar = el("div", { class: "rd-lens-bar", role: "region", "aria-label": "Manga Lens" },
      el("strong", { class: "rd-lens-title" }, "Manga Lens"),
      this.fromSel,
      el("span", { class: "rd-lens-arrow" }, "→"),
      this.toSel,
      aiLbl,
      this.runBtn,
      this.peekBtn,
      this.applyBtn,
      this.statusEl,
      closeBtn,
    );
    this.reader.root.append(this.bar);
  }

  setPeek(on) {
    this.peeking = !!on;
    if (this.layer) this.layer.classList.toggle("peeking", this.peeking);
    if (this.peekBtn) this.peekBtn.classList.toggle("seal", this.peeking);
  }

  #handleKeyDown(e) {
    if (!this.active) return;
    if (e.key === "Escape") {
      if (this.selectedId !== null) {
        this.selectedId = null;
        this.#renderOverlay();
      } else {
        this.close();
      }
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    const tag = e.target?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.target?.isContentEditable) return;
    if (e.code === "Space" && !e.repeat) {
      this.setPeek(true);
      e.preventDefault();
      e.stopPropagation();
    }
  }

  #handleKeyUp(e) {
    if (!this.active) return;
    if (e.code === "Space" && this.peeking) {
      this.setPeek(false);
      e.preventDefault();
      e.stopPropagation();
    }
  }

  async translateCurrentPage() {
    const r = this.reader;
    const tab = r.activeTab;
    if (!tab?.doc) return;
    const seq = (this._reqSeq = (this._reqSeq || 0) + 1);
    const pageNum = r.currentPage || 1;
    this.pageNum = pageNum;
    this.selectedId = null;
    this.runBtn.disabled = true;
    this.applyBtn.disabled = true;
    this.statusEl.textContent = `Translating page ${pageNum}…`;

    try {
      const fd = new FormData();
      if (tab.fileId && r.isPristine()) {
        fd.append("id", tab.fileId);
      } else {
        const bytes = await r._currentBytes();
        fd.append("file", new Blob([bytes], { type: "application/pdf" }), tab.name || "document.pdf");
      }
      fd.append("page", String(pageNum));
      fd.append("from", this.srcLang);
      fd.append("to", this.dstLang);
      fd.append("manga_ai", this.useAi ? "true" : "false");
      if (r.rtlReading) fd.append("reading_dir", "rtl");
      if (tab.password) fd.append("password", tab.password);

      const res = await fetch("/api/translate/manga-page", {
        method: "POST",
        headers: r.token ? { "X-Pdfm-Token": r.token } : {},
        body: fd,
      });
      if (!this.active || seq !== this._reqSeq) return;
      let data = {};
      try {
        data = await res.json();
      } catch {
        if (!res.ok) throw new Error(res.statusText || `HTTP ${res.status}`);
        throw new Error("Invalid response from server");
      }
      if (!res.ok) throw new Error(data.error || res.statusText || `HTTP ${res.status}`);

      this.bubbles = (data.bubbles || []).map((b, i) => ({ ...b, id: b.id ?? i }));
      this.applyBtn.disabled = this.bubbles.length === 0;
      this.statusEl.textContent = this.bubbles.length
        ? `${this.bubbles.length} bubble${this.bubbles.length === 1 ? "" : "s"} · click to edit · hold Space to peek`
        : "No speech bubbles found on this page.";
      this.#renderOverlay();
    } catch (err) {
      if (!this.active || seq !== this._reqSeq) return;
      this.statusEl.textContent = err?.message || String(err);
      r.toast(`Manga Lens: ${err?.message || err}`, 5000);
    } finally {
      if (this.runBtn && seq === this._reqSeq) this.runBtn.disabled = false;
    }
  }

  #clearOverlay() {
    if (this.layer) {
      this.layer.remove();
      this.layer = null;
    }
  }

  #pageEl() {
    if (!this.pageNum) return null;
    return this.reader.viewerEl?.querySelector(`.page[data-page-number="${this.pageNum}"]`) || null;
  }

  #renderOverlay() {
    this.#clearOverlay();
    if (!this.active || !this.bubbles.length) return;
    const pageEl = this.#pageEl();
    if (!pageEl) return;

    const layer = el("div", { class: "rd-manga-layer" + (this.peeking ? " peeking" : "") });
    layer.addEventListener("click", (e) => {
      if (e.target === layer && this.selectedId !== null) {
        this.selectedId = null;
        this.#renderOverlay();
      }
    });

    for (const b of this.bubbles) {
      const bg = b.bg_rgb || [1, 1, 1];
      const fg = b.text_rgb || [0.06, 0.06, 0.08];
      const bgCss = `rgb(${Math.round(bg[0] * 255)}, ${Math.round(bg[1] * 255)}, ${Math.round(bg[2] * 255)})`;
      const fgCss = `rgb(${Math.round(fg[0] * 255)}, ${Math.round(fg[1] * 255)}, ${Math.round(fg[2] * 255)})`;
      const isSelected = this.selectedId === b.id;

      const box = el("div", {
        class: "rd-manga-bubble" + (isSelected ? " selected" : "") + (b.shape === "rect" ? " rect" : " oval"),
        style: [
          `left:${(b.x0 * 100).toFixed(3)}%`,
          `top:${(b.y0 * 100).toFixed(3)}%`,
          `width:${Math.max(2, (b.x1 - b.x0) * 100).toFixed(3)}%`,
          `height:${Math.max(2, (b.y1 - b.y0) * 100).toFixed(3)}%`,
          `background:${bgCss}`,
          `color:${fgCss}`,
          `font-size:${Math.round(12 * (b.font_scale || 1))}px`,
        ].join(";"),
        title: b.original ? `Original: ${b.original}` : "Click to edit bubble",
      });

      const textEl = el("div", { class: "rd-manga-bubble-text" }, b.translated);
      box.append(textEl);

      box.addEventListener("click", (e) => {
        e.stopPropagation();
        if (this.selectedId !== b.id) {
          this.selectedId = b.id;
          this.#renderOverlay();
        }
      });

      if (isSelected) {
        textEl.contentEditable = "true";
        textEl.spellcheck = true;
        textEl.addEventListener("input", () => {
          b.translated = textEl.innerText;
        });
        textEl.addEventListener("click", (e) => e.stopPropagation());

        const tools = el("div", { class: "rd-manga-bubble-tools" });
        const smaller = el("button", { type: "button", title: "Smaller text" }, "A-");
        smaller.addEventListener("click", (e) => {
          e.stopPropagation();
          b.font_scale = Math.max(0.5, Math.round(((b.font_scale || 1) - 0.1) * 10) / 10);
          this.#renderOverlay();
        });
        const bigger = el("button", { type: "button", title: "Larger text" }, "A+");
        bigger.addEventListener("click", (e) => {
          e.stopPropagation();
          b.font_scale = Math.min(2.0, Math.round(((b.font_scale || 1) + 0.1) * 10) / 10);
          this.#renderOverlay();
        });
        const del = el("button", { type: "button", title: "Remove bubble" }, "×");
        del.addEventListener("click", (e) => {
          e.stopPropagation();
          this.bubbles = this.bubbles.filter((item) => item.id !== b.id);
          this.selectedId = null;
          this.applyBtn.disabled = this.bubbles.length === 0;
          this.#renderOverlay();
        });
        tools.append(smaller, bigger, del);
        box.append(tools);

        // Drag to move (via top border / tools bar)
        tools.addEventListener("pointerdown", (e) => {
          if (e.target.tagName === "BUTTON") return;
          e.preventDefault();
          e.stopPropagation();
          const rect = layer.getBoundingClientRect();
          const startX = e.clientX, startY = e.clientY;
          const orig = { x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 };
          const onMove = (ev) => {
            const dx = (ev.clientX - startX) / Math.max(1, rect.width);
            const dy = (ev.clientY - startY) / Math.max(1, rect.height);
            const w = orig.x1 - orig.x0, h = orig.y1 - orig.y0;
            b.x0 = Math.max(0, Math.min(1 - w, orig.x0 + dx));
            b.y0 = Math.max(0, Math.min(1 - h, orig.y0 + dy));
            b.x1 = b.x0 + w;
            b.y1 = b.y0 + h;
            box.style.left = `${(b.x0 * 100).toFixed(3)}%`;
            box.style.top = `${(b.y0 * 100).toFixed(3)}%`;
          };
          const onUp = () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            window.removeEventListener("pointercancel", onUp);
          };
          window.addEventListener("pointermove", onMove);
          window.addEventListener("pointerup", onUp);
          window.addEventListener("pointercancel", onUp);
        });

        // Bottom-right resize handle
        const handle = el("div", { class: "rd-manga-resize", title: "Drag to resize bubble" });
        handle.addEventListener("pointerdown", (e) => {
          e.preventDefault();
          e.stopPropagation();
          const rect = layer.getBoundingClientRect();
          const startX = e.clientX, startY = e.clientY;
          const origX1 = b.x1, origY1 = b.y1;
          const onMove = (ev) => {
            const dx = (ev.clientX - startX) / Math.max(1, rect.width);
            const dy = (ev.clientY - startY) / Math.max(1, rect.height);
            b.x1 = Math.max(b.x0 + 0.03, Math.min(1, origX1 + dx));
            b.y1 = Math.max(b.y0 + 0.03, Math.min(1, origY1 + dy));
            box.style.width = `${((b.x1 - b.x0) * 100).toFixed(3)}%`;
            box.style.height = `${((b.y1 - b.y0) * 100).toFixed(3)}%`;
          };
          const onUp = () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            window.removeEventListener("pointercancel", onUp);
          };
          window.addEventListener("pointermove", onMove);
          window.addEventListener("pointerup", onUp);
          window.addEventListener("pointercancel", onUp);
        });
        box.append(handle);
      }

      layer.append(box);
    }

    pageEl.append(layer);
    this.layer = layer;
  }

  async applyToPdf() {
    if (!this.active || !this.bubbles.length || !this.pageNum) return;
    const r = this.reader;
    const page = this.pageNum;
    const payload = JSON.stringify(this.bubbles);
    const dst = this.dstLang;
    const useAi = this.useAi;
    this.applyBtn.disabled = true;
    try {
      await r._changeDocument(
        (bytes, tab) => r._post("/api/translate/manga-page/apply", bytes, tab, {
          page: String(page),
          to: dst,
          manga_ai: useAi ? "true" : "false",
          bubbles: payload,
        }),
        {
          failure: "Could not apply Manga Lens translation: ",
          after: () => {
            this.close();
            r.toast(`Applied translated bubbles to page ${page}.`);
          },
        },
      );
    } finally {
      if (this.active && this.applyBtn) this.applyBtn.disabled = this.bubbles.length === 0;
    }
  }
}
