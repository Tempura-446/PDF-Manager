/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Check OCR mode in the reader: outlines words the OCR engine was unsure of,
// shows a cropped image of each word beside the recognised text, and lets the
// user correct or accept words one by one (Tab to the next) without touching
// the page image. A part of Reader (see CheckOcrMode below).

import { el, svg } from "./readerkit.js";

export const CheckOcrMode = (Base) => class extends Base {
  _buildCheckOcrBar() {
    this.checkOcrStatus = el("span", { class: "rd-ocr-status" }, "");
    this.checkOcrPrevBtn = el("button", {
      class: "rd-btn compact", type: "button",
      title: "Previous unsure word (Shift+Tab)", "aria-label": "Previous unsure word",
      html: svg("up"),
    });
    this.checkOcrNextBtn = el("button", {
      class: "rd-btn compact", type: "button",
      title: "Next unsure word (Tab)", "aria-label": "Next unsure word",
      html: svg("down"),
    });
    this.checkOcrAllBtn = el("button", {
      class: "rd-btn compact", type: "button",
      title: "Show all invisible OCR words (even high-confidence) to fix any typo",
      "aria-pressed": "false",
    }, "All words");
    this.checkOcrReplaceBtn = el("button", {
      class: "rd-btn compact", type: "button",
      title: "Find and replace text across the invisible OCR text layer",
    }, "Find & Replace");
    const done = el("button", { class: "rd-btn", type: "button", title: "Close Check OCR (Esc)" }, "Done");
    this.checkOcrPrevBtn.addEventListener("click", () => this._stepCheckOcr(-1));
    this.checkOcrNextBtn.addEventListener("click", () => this._stepCheckOcr(1));
    this.checkOcrAllBtn.addEventListener("click", () => this._toggleCheckOcrAllWords());
    this.checkOcrReplaceBtn.addEventListener("click", () => this._promptOcrFindReplace());
    done.addEventListener("click", () => this.closeCheckOcr());
    this.checkocrbar = el("div", { class: "rd-bar rd-ocr-bar", hidden: true },
      el("span", { class: "rd-ocr-title" }, "Check OCR"),
      this.checkOcrStatus,
      el("span", { class: "rd-sep" }),
      this.checkOcrPrevBtn,
      this.checkOcrNextBtn,
      this.checkOcrAllBtn,
      this.checkOcrReplaceBtn,
      el("span", { style: "flex:1" }),
      done);
    this.checkOcrMode = false;
    this._ocrAllWords = false;
    this._ocrUnsure = { pages: {}, total: 0 };
    this._ocrActive = null; // { page, id }
    this._ocrPop = null;
    this._ocrPendingStep = null;
  }

  /** Enter Check OCR mode for the active document. */
  async openCheckOcr(pageNumber = null, { autoOpen = true, allWords = false } = {}) {
    if (!this.doc) return;
    if (this.annotbar && !this.annotbar.hidden) this.closeAnnotate();
    if (this.formMode) this.closeFormMode();
    this.checkOcrMode = true;
    this._ocrAllWords = !!allWords;
    this.checkOcrAllBtn.setAttribute("aria-pressed", String(this._ocrAllWords));
    this.checkocrbar.hidden = false;
    this.container.classList.add("rd-check-ocr");
    this.checkOcrStatus.textContent = "Checking…";
    if (pageNumber && pageNumber >= 1 && pageNumber <= this.pdfViewer.pagesCount) {
      this.goToPage(pageNumber);
    }
    await this._loadCheckOcrWords();
    if (!this.checkOcrMode) return;
    this._drawCheckOcrOverlays();
    if (autoOpen) {
      const target = this._findCheckOcrTarget(pageNumber || this.pdfViewer.currentPageNumber);
      if (target) this._focusCheckOcrWord(target.page, target.word);
    }
  }

  async _toggleCheckOcrAllWords() {
    this._ocrAllWords = !this._ocrAllWords;
    this.checkOcrAllBtn.setAttribute("aria-pressed", String(this._ocrAllWords));
    this._closeCheckOcrPop();
    this.checkOcrStatus.textContent = "Loading OCR words…";
    await this._loadCheckOcrWords();
    if (this.checkOcrMode) this._drawCheckOcrOverlays();
  }

  async _promptOcrFindReplace() {
    if (!this.doc || !this.activeTab) return;
    const findText = window.prompt("Find text in invisible OCR layer:");
    if (!findText) return;
    const replaceText = window.prompt(`Replace “${findText}” with:`, findText);
    if (replaceText === null) return;
    try {
      let changed = 0;
      await this._changeDocument(async (bytes, tab) => {
        const res = await this._post("/api/ocr/replace", bytes, tab, {
          find: findText, replace: replaceText, case_sensitive: "false",
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
        changed = Number(res.headers.get("X-Pdfm-Changed") || 0);
        return new Uint8Array(await res.arrayBuffer());
      });
      await this._loadCheckOcrWords();
      if (this.checkOcrMode) this._drawCheckOcrOverlays();
      this.toast(changed ? `Replaced ${changed} OCR occurrence${changed === 1 ? "" : "s"}.` : `No OCR occurrences of “${findText}” found.`);
    } catch (e) {
      this.toast("OCR find & replace failed: " + (e.message || e));
    }
  }

  closeCheckOcr() {
    if (!this.checkOcrMode) return;
    this.checkOcrMode = false;
    this.checkocrbar.hidden = true;
    this.container.classList.remove("rd-check-ocr");
    this._closeCheckOcrPop();
    this._ocrActive = null;
    this._ocrPendingStep = null;
    for (const layer of this.viewerEl.querySelectorAll(".rd-ocr-layer")) layer.remove();
  }

  async _loadCheckOcrWords() {
    const tab = this.activeTab;
    if (!tab) return;
    try {
      const bytes = await this._currentBytes();
      const endpoint = this._ocrAllWords ? "/api/ocr/words" : "/api/ocr/unsure";
      const res = await this._post(endpoint, bytes, tab);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const data = await res.json();
      if (tab !== this.activeTab) return;
      this._ocrUnsure = data && data.pages ? data : { pages: {}, total: 0 };
    } catch (e) {
      this._ocrUnsure = { pages: {}, total: 0 };
      this.toast("Could not load OCR words: " + (e.message || e));
    }
    this._updateCheckOcrStatus();
  }

  _updateCheckOcrStatus() {
    const n = this._ocrUnsure?.total || 0;
    const pages = Object.keys(this._ocrUnsure?.pages || {}).length;
    if (n === 0) {
      this.checkOcrStatus.textContent = this._ocrAllWords
        ? "No invisible OCR text layer found on this PDF."
        : "No unsure words left — click “All words” to edit any OCR word.";
    } else {
      const kind = this._ocrAllWords ? "OCR word" : "unsure word";
      this.checkOcrStatus.textContent = `${n} ${kind}${n === 1 ? "" : "s"} on ${pages} page${pages === 1 ? "" : "s"} — click a box to fix, Tab for next`;
    }
    this.checkOcrPrevBtn.disabled = n === 0;
    this.checkOcrNextBtn.disabled = n === 0;
  }

  /** Flat ordered list of all unsure words: [{ page, word }]. */
  _allCheckOcrWords() {
    const pages = this._ocrUnsure?.pages || {};
    const nums = Object.keys(pages).map(Number).sort((a, b) => a - b);
    const list = [];
    for (const p of nums) {
      for (const w of pages[String(p)] || []) list.push({ page: p, word: w });
    }
    return list;
  }

  _findCheckOcrTarget(preferredPage) {
    const all = this._allCheckOcrWords();
    if (!all.length) return null;
    return all.find((it) => it.page === preferredPage) || all.find((it) => it.page > preferredPage) || all[0];
  }

  _drawCheckOcrOverlays(onlyPage = null) {
    if (!this.checkOcrMode || !this.pdfViewer) return;
    const count = this.pdfViewer.pagesCount || 0;
    const pages = this._ocrUnsure?.pages || {};
    for (let p = 1; p <= count; p++) {
      if (onlyPage !== null && p !== onlyPage) continue;
      const pv = this.pdfViewer.getPageView(p - 1);
      if (!pv?.div) continue;
      let layer = pv.div.querySelector(".rd-ocr-layer");
      const list = pages[String(p)] || [];
      if (!list.length) {
        if (layer) layer.remove();
        continue;
      }
      if (!layer) {
        layer = el("div", { class: "rd-ocr-layer" });
        pv.div.append(layer);
      }
      const nodes = [];
      for (const w of list) {
        const [x0, y0, x1, y1] = w.box || [0, 0, 0, 0];
        const [pw, ph] = w.page_size || [1, 1];
        if (pw <= 0 || ph <= 0) continue;
        const left = (x0 / pw) * 100;
        const top = (y0 / ph) * 100;
        const width = Math.max(((x1 - x0) / pw) * 100, 0.5);
        const height = Math.max(((y1 - y0) / ph) * 100, 0.5);
        const isActive = this._ocrActive && this._ocrActive.page === p && this._ocrActive.id === w.id;
        const btn = el("button", {
          type: "button",
          class: "rd-ocr-unsure" + (w.unsure === false ? " rd-ocr-word-ok" : "") + (isActive ? " active" : ""),
          "data-ocr-id": String(w.id),
          title: `“${w.text}” (${Math.round((w.confidence || 0) * 100)}% sure) — click to fix`,
          "aria-label": `Check OCR word ${w.text}`,
        });
        Object.assign(btn.style, {
          left: `${left}%`, top: `${top}%`, width: `${width}%`, height: `${height}%`,
        });
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          this._openCheckOcrWord(p, w, btn);
        });
        nodes.push(btn);
      }
      layer.replaceChildren(...nodes);
      // If we were waiting to open a word on this newly rendered page, open it now.
      if (this._ocrPendingStep && this._ocrPendingStep.page === p) {
        const target = this._ocrPendingStep;
        this._ocrPendingStep = null;
        const btn = layer.querySelector(`[data-ocr-id="${target.word.id}"]`);
        if (btn) this._openCheckOcrWord(p, target.word, btn);
      }
    }
  }

  _focusCheckOcrWord(pageNum, word) {
    this._ocrActive = { page: pageNum, id: word.id };
    if (this.pdfViewer.currentPageNumber !== pageNum) {
      this.goToPage(pageNum);
    }
    const pv = this.pdfViewer.getPageView(pageNum - 1);
    const btn = pv?.div?.querySelector(`.rd-ocr-layer [data-ocr-id="${word.id}"]`);
    if (btn && pv.div.querySelector("canvas")) {
      btn.scrollIntoView({ block: "center", inline: "nearest" });
      this._openCheckOcrWord(pageNum, word, btn);
    } else {
      this._ocrPendingStep = { page: pageNum, word };
      this._drawCheckOcrOverlays(pageNum);
    }
  }

  _stepCheckOcr(delta) {
    const all = this._allCheckOcrWords();
    if (!all.length) {
      this._closeCheckOcrPop();
      return;
    }
    let idx = -1;
    if (this._ocrActive) {
      idx = all.findIndex((it) => it.page === this._ocrActive.page && it.id === this._ocrActive.id
        || (it.page === this._ocrActive.page && it.word.id === this._ocrActive.id));
    }
    if (idx < 0) {
      idx = delta > 0 ? 0 : all.length - 1;
    } else {
      idx = (idx + delta + all.length) % all.length;
    }
    this._focusCheckOcrWord(all[idx].page, all[idx].word);
  }

  _closeCheckOcrPop() {
    if (this._ocrPop) {
      this._ocrPop.remove();
      this._ocrPop = null;
    }
    for (const b of this.viewerEl.querySelectorAll(".rd-ocr-unsure.active")) {
      b.classList.remove("active");
    }
  }

  _cropWordCanvas(pageNum, word) {
    const pv = this.pdfViewer.getPageView(pageNum - 1);
    const src = pv?.div?.querySelector("canvas");
    if (!src || !src.width || !src.height) return null;
    const [x0, y0, x1, y1] = word.box || [0, 0, 0, 0];
    const [pw, ph] = word.page_size || [1, 1];
    if (pw <= 0 || ph <= 0) return null;
    const padX = Math.max((x1 - x0) * 0.25, 6);
    const padY = Math.max((y1 - y0) * 0.45, 5);
    const sx = Math.max(0, Math.floor(((x0 - padX) / pw) * src.width));
    const sy = Math.max(0, Math.floor(((y0 - padY) / ph) * src.height));
    const ex = Math.min(src.width, Math.ceil(((x1 + padX) / pw) * src.width));
    const ey = Math.min(src.height, Math.ceil(((y1 + padY) / ph) * src.height));
    const sw = Math.max(1, ex - sx);
    const sh = Math.max(1, ey - sy);
    const maxW = 260, maxH = 72;
    const scale = Math.min(maxW / sw, maxH / sh, 2.5);
    const out = document.createElement("canvas");
    out.className = "rd-ocr-crop";
    out.width = Math.max(1, Math.round(sw * scale));
    out.height = Math.max(1, Math.round(sh * scale));
    const ctx = out.getContext("2d");
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(src, sx, sy, sw, sh, 0, 0, out.width, out.height);
    return out;
  }

  _openCheckOcrWord(pageNum, word, boxEl) {
    this._closeCheckOcrPop();
    this._ocrActive = { page: pageNum, id: word.id };
    if (boxEl) boxEl.classList.add("active");

    const crop = this._cropWordCanvas(pageNum, word);
    const pct = Math.round((word.confidence || 0) * 100);
    const input = el("input", {
      type: "text", class: "rd-ocr-input", value: word.text || "",
      "aria-label": "Correct recognised word",
    });
    const saveBtn = el("button", { type: "button", class: "rd-btn rd-ocr-save" }, "Save");
    const keepBtn = el("button", { type: "button", class: "rd-btn secondary" }, "Keep as is");
    const skipBtn = el("button", { type: "button", class: "rd-btn secondary", title: "Skip to next (Tab)" }, "Next");

    const commit = (acceptIfUnchanged, stepDir = 1) => {
      const nextText = input.value.trim();
      if (!nextText) {
        input.focus();
        return;
      }
      if (nextText !== word.text) {
        this._saveCheckOcrEdit({ page: pageNum, id: word.id, text: nextText }, stepDir);
      } else if (acceptIfUnchanged) {
        this._saveCheckOcrEdit({ page: pageNum, id: word.id, accept: true }, stepDir);
      } else {
        this._stepCheckOcr(stepDir);
      }
    };

    saveBtn.addEventListener("click", () => commit(true, 1));
    keepBtn.addEventListener("click", () => this._saveCheckOcrEdit({ page: pageNum, id: word.id, accept: true }, 1));
    skipBtn.addEventListener("click", () => this._stepCheckOcr(1));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        commit(true, 1);
      } else if (e.key === "Tab") {
        e.preventDefault();
        commit(false, e.shiftKey ? -1 : 1);
      } else if (e.key === "Escape") {
        e.preventDefault();
        this._closeCheckOcrPop();
      }
    });

    const pop = el("div", { class: "rd-ocr-pop", role: "dialog", "aria-label": "Check OCR word" },
      el("div", { class: "rd-ocr-pop-head" },
        el("span", {}, `Page ${pageNum} · ${pct}% sure`),
        el("button", {
          type: "button", class: "rd-ocr-x", "aria-label": "Close",
          onclick: () => this._closeCheckOcrPop(),
        }, "×")),
      crop ? el("div", { class: "rd-ocr-crop-wrap" }, crop) : null,
      el("div", { class: "rd-ocr-field" },
        el("label", {}, "Recognised text", input)),
      el("div", { class: "rd-ocr-actions" }, saveBtn, keepBtn, skipBtn));

    const pv = this.pdfViewer.getPageView(pageNum - 1);
    const host = pv?.div || this.viewerEl;
    host.append(pop);
    this._ocrPop = pop;

    // Position right below (or above) the word box inside the page.
    if (boxEl) {
      const topPct = parseFloat(boxEl.style.top) || 0;
      const heightPct = parseFloat(boxEl.style.height) || 0;
      const leftPct = parseFloat(boxEl.style.left) || 0;
      const placeAbove = topPct > 65;
      pop.style.left = `clamp(8px, ${leftPct}%, calc(100% - 300px))`;
      if (placeAbove) {
        pop.style.bottom = `calc(${100 - topPct}% + 6px)`;
      } else {
        pop.style.top = `calc(${topPct + heightPct}% + 6px)`;
      }
    }
    input.focus();
    input.select();
  }

  _saveCheckOcrEdit(edit, stepDir = 1) {
    // Remember which unsure word comes next before reloading the document.
    const all = this._allCheckOcrWords();
    const curIdx = all.findIndex((it) => it.page === edit.page && it.word.id === edit.id);
    const remaining = all.filter((_, i) => i !== curIdx);
    let nextTarget = null;
    if (remaining.length) {
      const nextIdx = curIdx < 0 ? 0 : (stepDir > 0 ? curIdx % remaining.length : (curIdx - 1 + remaining.length) % remaining.length);
      nextTarget = { page: remaining[nextIdx].page, id: remaining[nextIdx].word.id };
    }
    this._closeCheckOcrPop();
    this._ocrNextAfterReload = nextTarget;
    return this._changeDocument(
      (bytes, tab) => this._post("/api/ocr/fix", bytes, tab, { edits: JSON.stringify([edit]) }),
      {
        failure: "Could not fix OCR text: ",
        kind: "text",
      },
    );
  }
};
