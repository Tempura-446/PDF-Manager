/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Prepare form mode in the reader: draw, select, move, resize, nudge,
// duplicate, copy/paste and delete form fields over the page, and run Find
// fields, with every change going through the reader's Undo/Redo history.
// A part of Reader (see FormMode below).

import { el, isTyping, svg } from "./readerkit.js";

const FIELD_TOOLS = [
  ["select", "Select", "Click to select, drag a box to select several, drag a field to move it", "pointer"],
  ["text", "Text", "Add a single-line text box", "text"],
  ["multiline", "Multi-line", "Add a multi-line text box", "text"],
  ["checkbox", "Checkbox", "Add a tick box", "checkbox"],
  ["radio", "Radio", "Add a radio button", "radio"],
  ["dropdown", "Dropdown", "Add a dropdown list", "dropdown"],
  ["list", "List", "Add a scrollable list box", "list"],
  ["date", "Date", "Add a date field", "date"],
  ["signature", "Signature", "Add a signature field", "sign"],
  ["button", "Button", "Add a button", "button"],
];

const DEFAULT_SIZE = {
  text: [144, 22],
  multiline: [160, 60],
  checkbox: [16, 16],
  radio: [16, 16],
  dropdown: [144, 22],
  list: [144, 60],
  date: [110, 22],
  signature: [160, 44],
  button: [90, 24],
};

const NUDGE_REST = 350;  // ms without an arrow key before nudges are written

const fieldKey = (page, index) => `${page}:${index}`;

function viewRectFromPdf(viewport, [x0, y0, x1, y1]) {
  const [ax, ay] = viewport.convertToViewportPoint(x0, y0);
  const [bx, by] = viewport.convertToViewportPoint(x1, y1);
  const left = Math.min(ax, bx), top = Math.min(ay, by);
  return { left, top, width: Math.abs(bx - ax), height: Math.abs(by - ay) };
}

function pdfRectFromView(viewport, left, top, right, bottom) {
  const [ax, ay] = viewport.convertToPdfPoint(left, top);
  const [bx, by] = viewport.convertToPdfPoint(right, bottom);
  return [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];
}

function clampRect(rect, bounds) {
  if (!bounds) return rect;
  const [bx0, by0, bx1, by1] = bounds;
  const w = Math.min(rect[2] - rect[0], bx1 - bx0);
  const h = Math.min(rect[3] - rect[1], by1 - by0);
  const x0 = Math.max(bx0, Math.min(rect[0], bx1 - w));
  const y0 = Math.max(by0, Math.min(rect[1], by1 - h));
  return [x0, y0, x0 + w, y0 + h];
}

function specForTool(tool, page, rect, source = null) {
  if (source) {
    const spec = { page, rect, type: source.type === "other" ? "text" : source.type };
    if (source.name) spec.name = source.name;
    if (source.options?.length) spec.options = [...source.options];
    if (source.format) spec.format = source.format;
    if (source.date) spec.date = source.date;
    if (source.required) spec.required = true;
    return spec;
  }
  if (tool === "date") return { page, rect, type: "text", format: "date", date: "yyyy-mm-dd", name: "Date" };
  if (tool === "dropdown" || tool === "list") return { page, rect, type: tool, options: ["Option 1", "Option 2"] };
  return { page, rect, type: tool };
}

export const FormMode = (Base) => class extends Base {
  _buildFormBar() {
    this.formToolBtns = FIELD_TOOLS.map(([id, label, title, icon]) => {
      const btn = el("button", {
        class: "rd-btn rd-tool", type: "button", "data-form-tool": id,
        "aria-pressed": String(id === "select"), title,
        html: (icon && svg(icon) ? svg(icon) : "") + `<span>${label}</span>`,
      });
      btn.addEventListener("click", () => this._setFormTool(id));
      return btn;
    });
    this.formFindBtn = el("button", {
      class: "rd-btn", type: "button",
      title: "Detect lines, boxes and checkboxes on the pages and add them as fields",
      html: svg("fillable") + "<span>Find fields</span>",
    });
    this.formFindBtn.addEventListener("click", () => this.makeFillable());
    this.formUndoBtn = el("button", { class: "rd-btn compact", type: "button", title: "Undo (Ctrl+Z)", "aria-label": "Undo", disabled: true, html: svg("undo") });
    this.formRedoBtn = el("button", { class: "rd-btn compact", type: "button", title: "Redo (Ctrl+Y)", "aria-label": "Redo", disabled: true, html: svg("redo") });
    this.formDeleteBtn = el("button", { class: "rd-btn compact", type: "button", title: "Delete selected fields (Delete)", "aria-label": "Delete selected fields", disabled: true, html: svg("trash") });
    this.formSaveBtn = el("button", { class: "rd-btn", type: "button", title: "Save changes (Ctrl+S)", "aria-label": "Save", html: svg("save") + "<span>Save</span>" });
    this.formPreviewBtn = el("button", { class: "rd-btn", type: "button", "aria-pressed": "false", title: "Try filling in the form with scripts running" }, "Preview");
    this.formKeepDefaultsBtn = el("button", { class: "rd-btn", type: "button", hidden: true, title: "Save what you entered in Preview as the fields' default values" }, "Keep as default values");
    this.formCheckBtn = el("button", { class: "rd-btn", type: "button", title: "Check for missing tooltips, overlaps, or broken calculations" }, "Check form");
    this.formDataBtn = el("button", { class: "rd-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", title: "Clear, import, or export form data" }, "Data ▾");
    this.formDataInput = el("input", { type: "file", accept: ".fdf,.xfdf,.xml,.csv,.json", hidden: true });
    this.formDataInput.addEventListener("change", () => {
      const f = this.formDataInput.files?.[0];
      this.formDataInput.value = "";
      if (f) this.importFormData(f);
    });
    const mkItem = (label, fn) => {
      const b = el("button", { type: "button", role: "menuitem" }, label);
      b.addEventListener("click", () => { this._closeFormDataMenu(); fn(); });
      return b;
    };
    this.formDataMenu = el("div", { class: "rd-menu rd-formdatamenu", role: "menu", "aria-label": "Form data", hidden: true },
      mkItem("Clear form", () => this.clearForm()),
      mkItem("Import data…", () => this.formDataInput.click()),
      el("hr"),
      el("div", { class: "label" }, "Export data"),
      mkItem("FDF (.fdf)", () => this.exportFormData("fdf")),
      mkItem("XFDF (.xfdf)", () => this.exportFormData("xfdf")),
      mkItem("CSV (.csv)", () => this.exportFormData("csv")),
      mkItem("JSON (.json)", () => this.exportFormData("json")),
    );
    this.formCloseBtn = el("button", { class: "rd-btn", type: "button", title: "Close Prepare form", "aria-label": "Close Prepare form", html: svg("x") });
    this.formUndoBtn.addEventListener("click", () => this._undo());
    this.formRedoBtn.addEventListener("click", () => this._undo(true));
    this.formDeleteBtn.addEventListener("click", () => this._deleteSelectedFields());
    this.formPreviewBtn.addEventListener("click", () => this.setFormPreview(!this.formPreview));
    this.formKeepDefaultsBtn.addEventListener("click", () => this.keepPreviewDefaults());
    this.formCheckBtn.addEventListener("click", () => this.checkForm());
    this.formDataBtn.addEventListener("click", (e) => { e.stopPropagation(); this._toggleFormDataMenu(); });
    this.formSaveBtn.addEventListener("click", () => this.save());
    this.formCloseBtn.addEventListener("click", () => this.closeFormMode());
    this.formmodebar = el("div", { class: "rd-findbar rd-formmodebar", hidden: true, role: "toolbar", "aria-label": "Prepare form" },
      ...this.formToolBtns, el("span", { class: "rd-sep" }), this.formFindBtn,
      el("span", { class: "rd-sep" }), this.formUndoBtn, this.formRedoBtn, this.formDeleteBtn,
      el("span", { class: "rd-sep" }), this.formPreviewBtn, this.formKeepDefaultsBtn, this.formCheckBtn, this.formDataBtn, this.formDataInput,
      el("span", { style: "flex:1" }), this.formSaveBtn, this.formCloseBtn);
    this.formMode = false;
    this.formPreview = false;
    this.formTool = "select";
    this.formPicked = new Set();
    this.formClipboard = null;
    this._formNudge = null;
    this._formDrag = null;
    this._formPointerWired = false;
  }

  openFormMode(tool = "select") {
    if (!this.doc) return;
    this._initFormPointer();
    if (!this.annotbar.hidden) this.closeAnnotate();
    this.measureTool?.(null);
    this.formbar.hidden = true;
    this.formMode = true;
    this.formPreview = false;
    this.formPicked = new Set();
    this.root.classList.add("rd-formmode");
    this.root.classList.remove("rd-form-preview");
    this.formmodebar.hidden = false;
    this.formPreviewBtn?.setAttribute("aria-pressed", "false");
    if (this.formKeepDefaultsBtn) this.formKeepDefaultsBtn.hidden = true;
    for (const btn of this.formToolBtns) btn.disabled = false;
    if (this.formFindBtn) this.formFindBtn.disabled = false;
    if (this.fieldsTab) this.fieldsTab.hidden = false;
    this._setFormTool(tool);
    this._stepAside();
    this._formUndoButtons();
    this._drawFormOverlays();
    this._geometry?.(this.pdfViewer.currentPageNumber || 1);
  }

  closeFormMode() {
    if (!this.formMode) return;
    this._formFlushNudge();
    this._closeFormDataMenu();
    const wasPreview = this.formPreview;
    this._formPickCb = null;
    this.formMode = false;
    this.formPreview = false;
    this.formTool = "select";
    this.formPicked = new Set();
    this.root.classList.remove("rd-formmode", "rd-form-placing", "rd-form-picking", "rd-form-preview");
    this.formmodebar.hidden = true;
    this.formPreviewBtn?.setAttribute("aria-pressed", "false");
    if (this.formKeepDefaultsBtn) this.formKeepDefaultsBtn.hidden = true;
    for (const btn of this.formToolBtns) btn.disabled = false;
    if (this.formFindBtn) this.formFindBtn.disabled = false;
    if (this.fieldsTab) {
      if (this.fieldsTab.getAttribute("aria-selected") === "true") this._showSidebarTab?.("thumbs");
      this.fieldsTab.hidden = true;
    }
    for (const layer of this.viewerEl.querySelectorAll(".rd-formlayer")) layer.remove();
    this._stepAside();
    this._notifyFormSelect();
    this.onFormCheck?.(null, this.activeTab);
    if (wasPreview && this.doc?.annotationStorage?.size > 0) this._reloadFormState?.();
  }

  async setFormPreview(on) {
    if (!this.formMode || !this.doc) return;
    const next = !!on;
    if (next === this.formPreview) return;
    this._formFlushNudge();
    this.formPreview = next;
    this.root.classList.toggle("rd-form-preview", next);
    this.formPreviewBtn?.setAttribute("aria-pressed", String(next));
    if (this.formKeepDefaultsBtn) this.formKeepDefaultsBtn.hidden = !next;
    for (const btn of this.formToolBtns) btn.disabled = next;
    if (this.formFindBtn) this.formFindBtn.disabled = next;
    if (next) {
      this._setFormTool("select");
      this.formPicked = new Set();
      this._notifyFormSelect();
      if (this.formDeleteBtn) this.formDeleteBtn.disabled = true;
    } else {
      if (this.doc.annotationStorage?.size > 0) await this._reloadFormState?.();
      else this._drawFormOverlays();
    }
  }

  keepPreviewDefaults() {
    if (!this.formMode || !this.doc) return;
    return this._changeDocument((bytes, tab) => this._post("/api/form/keep-defaults", bytes, tab), {
      failure: "Couldn't keep default values: ",
      edited: (_res, tab) => { tab.formFields = null; },
      after: (res) => {
        const count = Number(res.headers.get("X-Pdfm-Fields") || 0);
        this.toast(count ? `Saved ${count} default value${count === 1 ? "" : "s"}` : "Default values updated", 2000);
      },
    });
  }

  async checkForm() {
    if (!this.formMode || !this.doc) return;
    const tab = this.activeTab;
    try {
      const res = await this._post("/api/form/check", await this._currentBytes(), tab);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const { issues } = await res.json();
      if (this.activeTab === tab && this.formMode) this.onFormCheck?.(issues || [], tab);
    } catch (e) {
      this.toast("Couldn't check form: " + (e.message || e));
    }
  }

  async selectFormField(page, index) {
    if (!this.formMode || !this.doc) return;
    if (this.formPreview) await this.setFormPreview(false);
    this.formPicked = new Set([fieldKey(page, index)]);
    this.pdfViewer.currentPageNumber = page;
    this._drawFormOverlays();
    const box = this.viewerEl.querySelector(`.rd-formfield[data-page="${page}"][data-index="${index}"]`);
    box?.scrollIntoView({ block: "center", inline: "nearest" });
  }

  _closeFormDataMenu() {
    if (!this.formDataMenu || this.formDataMenu.hidden) return;
    this.formDataMenu.hidden = true;
    this.formDataBtn?.setAttribute("aria-expanded", "false");
    if (this._formDataOutside) {
      document.removeEventListener("pointerdown", this._formDataOutside, true);
      this._formDataOutside = null;
    }
  }

  _toggleFormDataMenu() {
    if (!this.formDataMenu || !this.formDataBtn) return;
    if (!this.formDataMenu.hidden) { this._closeFormDataMenu(); return; }
    if (!this.formDataMenu.parentElement) this.root.append(this.formDataMenu);
    const r = this.formDataBtn.getBoundingClientRect(), rr = this.root.getBoundingClientRect();
    this.formDataMenu.style.left = `${Math.max(8, r.left - rr.left)}px`;
    this.formDataMenu.style.right = "auto";
    this.formDataMenu.style.top = `${r.bottom - rr.top + 4}px`;
    this.formDataMenu.hidden = false;
    this.formDataBtn.setAttribute("aria-expanded", "true");
    this._formDataOutside = (e) => {
      if (!this.formDataMenu.contains(e.target) && e.target !== this.formDataBtn) this._closeFormDataMenu();
    };
    document.addEventListener("pointerdown", this._formDataOutside, true);
  }

  clearForm() {
    if (!this.formMode || !this.doc) return;
    return this._changeDocument((bytes, tab) => this._post("/api/form/clear", bytes, tab), {
      failure: "Couldn't clear form: ",
      edited: (_res, tab) => { tab.formFields = null; },
      after: (res) => {
        const count = Number(res.headers.get("X-Pdfm-Fields") || 0);
        this.toast(count ? `Cleared ${count} field${count === 1 ? "" : "s"}` : "Form cleared", 2000);
      },
    });
  }

  async exportFormData(fmt = "json") {
    if (!this.formMode || !this.doc) return;
    const tab = this.activeTab;
    try {
      const res = await this._post("/api/form/export", await this._currentBytes(), tab, { format: fmt });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const blob = await res.blob();
      const stem = (this.name || "form").replace(/\.pdf$/i, "");
      const filename = `${stem}.${fmt}`;
      const api = this.api?.();
      if (this.isDesktop?.() && api?.save_result) {
        await this._saveBytes(new Uint8Array(await blob.arrayBuffer()), filename);
        return;
      }
      const url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: filename, hidden: true });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      this.toast("Couldn't export form data: " + (e.message || e));
    }
  }

  importFormData(file) {
    if (!this.formMode || !this.doc || !file) return;
    return this._changeDocument((bytes, tab) => this._post("/api/form/import", bytes, tab, {}, [file]), {
      failure: "Couldn't import form data: ",
      edited: (_res, tab) => { tab.formFields = null; },
      after: (res) => {
        const count = Number(res.headers.get("X-Pdfm-Fields") || 0);
        this.toast(count ? `Filled ${count} field${count === 1 ? "" : "s"}` : "Form data imported", 2000);
      },
    });
  }

  _setFormTool(tool) {
    this.formTool = FIELD_TOOLS.some(([id]) => id === tool) ? tool : "select";
    for (const btn of this.formToolBtns) btn.setAttribute("aria-pressed", String(btn.dataset.formTool === this.formTool));
    this.root.classList.toggle("rd-form-placing", this.formMode && this.formTool !== "select");
  }

  _formUndoButtons() {
    if (!this.formUndoBtn) return;
    this.formUndoBtn.disabled = !this._undoKind();
    this.formRedoBtn.disabled = !this._undoKind(true);
    this.formDeleteBtn.disabled = !this.formPicked?.size;
  }

  _formFields() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return null;
    const version = tab.version || 0;
    if (tab.formFields?.version === version) return tab.formFields.pages;
    if (this._formAsking?.tab === tab && this._formAsking.version === version) return null;
    const asking = this._formAsking = { tab, version };
    (async () => {
      let pages = null, stale = false;
      try {
        const res = await this._post("/api/form/fields", await this._currentBytes(), tab, { details: "1" });
        if (res.ok) ({ fields: pages } = await res.json());
        stale = this.activeTab === tab && (tab.version || 0) !== version;
      } catch (e) { /* ignored */ } finally {
        if (this._formAsking === asking) this._formAsking = null;
      }
      if (stale) { this._formFields(); return; }
      if ((tab.version || 0) === version) tab.formFields = { version, pages: pages || {} };
      if (this.activeTab === tab && this.formMode) this._drawFormOverlays();
    })();
    return null;
  }

  _drawFormOverlays() {
    if (!this.formMode || !this.doc) return;
    const pages = this._formFields();
    if (!pages) return;
    for (let n = 1; n <= this.pdfViewer.pagesCount; n++) {
      const view = this.pdfViewer.getPageView(n - 1);
      if (!view?.div || !view.viewport) continue;
      let layer = view.div.querySelector(".rd-formlayer");
      if (!layer) {
        layer = el("div", { class: "rd-formlayer", "data-page": String(n) });
        view.div.append(layer);
      }
      const list = pages[String(n)] || [];
      const nodes = [];
      for (let i = 0; i < list.length; i++) {
        const f = list[i];
        const r = viewRectFromPdf(view.viewport, f.rect);
        const key = fieldKey(n, f.index);
        const sel = this.formPicked.has(key);
        const box = el("div", {
          class: "rd-formfield" + (sel ? " selected" : "") + (f.signed ? " signed" : ""),
          "data-page": String(n), "data-index": String(f.index),
          "data-type": f.type, "data-name": f.name, "data-order": String(i + 1),
          style: `left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px`,
          title: `${i + 1}. ${f.name} (${f.type})`,
        }, el("span", { class: "rd-formlabel" }, f.name), el("span", { class: "rd-formbadge" }, String(i + 1)));
        if (sel && this.formPicked.size === 1 && !f.signed) {
          for (const h of ["nw", "ne", "sw", "se"]) box.append(el("div", { class: `rd-formhandle ${h}`, "data-handle": h }));
        }
        nodes.push(box);
      }
      layer.replaceChildren(...nodes);
    }
    this._formUndoButtons();
    this._renderFieldsSidebar();
    this._notifyFormSelect();
  }

  _renderFieldsSidebar() {
    if (!this.fieldsPanel) return;
    if (!this.formMode || !this.doc) { this.fieldsPanel.replaceChildren(); return; }
    const pages = this._formFields() || {};
    const frag = document.createDocumentFragment();
    const curPage = this.pdfViewer.currentPageNumber || 1;
    const rowsBtn = el("button", { class: "rd-btn", type: "button" }, "Order by rows");
    rowsBtn.addEventListener("click", () => this.reorderFormFields(this.pdfViewer.currentPageNumber || curPage, null, "rows"));
    const colsBtn = el("button", { class: "rd-btn", type: "button" }, "Order by columns");
    colsBtn.addEventListener("click", () => this.reorderFormFields(this.pdfViewer.currentPageNumber || curPage, null, "cols"));
    frag.append(el("div", { class: "rd-fields-actions" }, rowsBtn, colsBtn));

    const pageNums = Object.keys(pages).map(Number).filter((n) => (pages[String(n)] || []).length).sort((a, b) => a - b);
    if (!pageNums.length) {
      frag.append(el("p", { style: "color:var(--muted);margin:8px 2px" }, "No fields yet. Pick a tool above and click or drag on a page."));
    }
    for (const pno of pageNums) {
      const list = pages[String(pno)] || [];
      const tabKind = list[0]?.tab_order || "rows";
      const tabLabel = { rows: "Rows", cols: "Columns", structure: "Structure", custom: "Custom" }[tabKind] || "Rows";
      const ol = el("ol", { class: "rd-fields-list", "data-page": String(pno) });
      let dragIdx = -1;
      list.forEach((f, pos) => {
        const key = fieldKey(pno, f.index);
        const nameSpan = el("span", { class: "rd-fielditem-name", title: "Click to select, double-click to rename" }, f.name);
        const li = el("li", {
          class: "rd-fielditem" + (this.formPicked.has(key) ? " selected" : ""),
          draggable: "true", "data-page": String(pno), "data-index": String(f.index),
        },
          el("span", { class: "rd-fielditem-num" }, String(pos + 1)),
          nameSpan,
          el("span", { class: "rd-fielditem-type" }, f.type));

        li.addEventListener("click", (e) => {
          if (e.target.tagName === "INPUT") return;
          if (!e.shiftKey && this.formPicked.size === 1 && this.formPicked.has(key)) return;
          if (e.shiftKey) {
            if (this.formPicked.has(key)) this.formPicked.delete(key);
            else this.formPicked.add(key);
          } else {
            this.formPicked = new Set([key]);
          }
          if (this.pdfViewer.currentPageNumber !== pno) this.pdfViewer.currentPageNumber = pno;
          this._drawFormOverlays();
        });

        nameSpan.addEventListener("dblclick", (e) => {
          e.stopPropagation();
          const inp = el("input", { class: "rd-fielditem-edit", type: "text", value: f.name });
          nameSpan.replaceWith(inp);
          inp.focus();
          inp.select();
          let done = false;
          const finish = (save) => {
            if (done) return;
            done = true;
            const next = inp.value.trim();
            if (save && next && next !== f.name) {
              this._editFormFields([{ page: pno, index: f.index, name: next }], { keepSelection: true });
            } else {
              inp.replaceWith(nameSpan);
            }
          };
          inp.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
            else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
          });
          inp.addEventListener("blur", () => finish(true));
        });

        li.addEventListener("dragstart", (e) => { dragIdx = pos; e.dataTransfer?.setData("text/plain", String(pos)); });
        li.addEventListener("dragenter", (e) => e.preventDefault());
        li.addEventListener("dragover", (e) => e.preventDefault());
        li.addEventListener("drop", (e) => {
          e.preventDefault();
          const from = dragIdx >= 0 ? dragIdx : parseInt(e.dataTransfer?.getData("text/plain") || "-1", 10);
          dragIdx = -1;
          if (from < 0 || Number.isNaN(from) || from === pos) return;
          const order = list.map((x) => x.index);
          const [moved] = order.splice(from, 1);
          order.splice(pos, 0, moved);
          this.reorderFormFields(pno, order, "custom");
        });
        ol.append(li);
      });
      frag.append(el("div", { class: "rd-fields-page" },
        el("div", { class: "rd-fields-pagehead" }, el("span", {}, `Page ${pno}`), el("span", {}, tabLabel)),
        ol));
    }
    this.fieldsPanel.replaceChildren(frag);
  }

  reorderFormFields(page, order, mode = "custom") {
    if (!this.doc) return;
    return this._changeDocument((bytes, tab) => this._post("/api/form/reorder", bytes, tab, {
      page: String(page), mode: String(mode), order: JSON.stringify(order ?? null),
    }), {
      failure: "Couldn't reorder fields: ",
      edited: (_res, tab) => { tab.formFields = null; },
      after: () => this._drawFormOverlays(),
    });
  }

  _notifyFormSelect() {
    this.onFormSelect?.(this.formMode ? this._selectedFields() : [], this.activeTab?.formFields?.pages || {}, this.activeTab);
  }

  setFormPickMode(cb) {
    this._formPickCb = cb || null;
    this.root.classList.toggle("rd-form-picking", !!this._formPickCb);
  }

  async formFonts() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return null;
    const version = tab.version || 0;
    if (tab.formFonts?.version === version) return tab.formFonts.choices;
    try {
      const res = await this._post("/api/text/fonts", await this._currentBytes(), tab);
      if (res.ok) {
        const choices = await res.json();
        if ((tab.version || 0) === version) tab.formFonts = { version, choices };
        return choices;
      }
    } catch (e) { /* ignored */ }
    return null;
  }

  _selectedFields() {
    const pages = this._formFields() || {};
    const out = [];
    for (const key of this.formPicked) {
      const [pStr, iStr] = key.split(":");
      const page = Number(pStr), index = Number(iStr);
      const item = (pages[String(page)] || []).find((f) => f.index === index);
      if (item) out.push({ page, ...item });
    }
    return out;
  }

  _initFormPointer() {
    if (this._formPointerWired || !this.viewerEl) return;
    this._formPointerWired = true;
    this.viewerEl.addEventListener("pointerdown", (e) => {
      if (!this.formMode || !this.doc || e.button !== 0) return;
      const pageEl = e.target.closest?.(".page");
      const page = Number(pageEl?.dataset.pageNumber);
      const view = page && this.pdfViewer.getPageView(page - 1);
      if (!page || !view?.viewport) return;
      const pr = pageEl.getBoundingClientRect();

      if (this.formTool !== "select") {
        e.preventDefault(); e.stopPropagation();
        const startX = e.clientX - pr.left, startY = e.clientY - pr.top;
        let layer = pageEl.querySelector(".rd-formlayer");
        if (!layer) { layer = el("div", { class: "rd-formlayer", "data-page": String(page) }); pageEl.append(layer); }
        const draft = el("div", { class: "rd-formdraft", style: `left:${startX}px;top:${startY}px;width:0;height:0` });
        layer.append(draft);
        this._formDrag = { kind: "add", page, pageEl, view, pr, startX, startY, curX: startX, curY: startY, draft, tool: this.formTool };
        return;
      }

      const handleEl = e.target.closest?.(".rd-formhandle");
      const fieldEl = e.target.closest?.(".rd-formfield");
      if (handleEl && fieldEl) {
        e.preventDefault(); e.stopPropagation();
        const idx = Number(fieldEl.dataset.index);
        const f = (this._formFields()?.[String(page)] || []).find((x) => x.index === idx);
        if (!f) return;
        const guides = this.#formGuides(page, view, new Set([idx]));
        this._formDrag = {
          kind: "resize", page, pageEl, view, pr, fieldEl, field: f, handle: handleEl.dataset.handle,
          orig: viewRectFromPdf(view.viewport, f.rect), guides,
          startX: e.clientX, startY: e.clientY, dx: 0, dy: 0,
        };
        return;
      }

      if (fieldEl) {
        e.preventDefault(); e.stopPropagation();
        const fPage = Number(fieldEl.dataset.page), fIdx = Number(fieldEl.dataset.index);
        if (this._formPickCb) {
          const f = (this._formFields()?.[String(fPage)] || []).find((x) => x.index === fIdx);
          if (f) this._formPickCb({ page: fPage, ...f });
          return;
        }
        const key = fieldKey(fPage, fIdx);
        if (e.shiftKey) {
          if (this.formPicked.has(key)) this.formPicked.delete(key);
          else this.formPicked.add(key);
          this._drawFormOverlays();
          return;
        }
        const narrowTo = (this.formPicked.has(key) && this.formPicked.size > 1) ? key : null;
        if (!this.formPicked.has(key)) {
          this.formPicked = new Set([key]);
          this._drawFormOverlays();
        }
        const moving = this._selectedFields().filter((f) => f.page === fPage);
        const els = moving.map((f) => ({
          field: f,
          el: pageEl.querySelector(`.rd-formfield[data-index="${f.index}"]`),
          orig: viewRectFromPdf(view.viewport, f.rect),
        })).filter((m) => m.el);
        const guides = this.#formGuides(fPage, view, new Set(moving.map((f) => f.index)));
        this._formDrag = { kind: "move", page: fPage, pageEl, view, els, guides, narrowTo, startX: e.clientX, startY: e.clientY, dx: 0, dy: 0 };
        return;
      }

      // Drag box (marquee) on empty page area
      e.preventDefault();
      const startX = e.clientX - pr.left, startY = e.clientY - pr.top;
      let layer = pageEl.querySelector(".rd-formlayer");
      if (!layer) { layer = el("div", { class: "rd-formlayer", "data-page": String(page) }); pageEl.append(layer); }
      const marquee = el("div", { class: "rd-formmarquee", style: `left:${startX}px;top:${startY}px;width:0;height:0` });
      layer.append(marquee);
      this._formDrag = { kind: "marquee", page, pageEl, pr, startX, startY, curX: startX, curY: startY, marquee, additive: e.shiftKey };
    });

    window.addEventListener("pointermove", (e) => {
      const d = this._formDrag;
      if (!d) return;
      if (d.kind === "add" || d.kind === "marquee") {
        d.curX = Math.max(0, Math.min(d.pr.width, e.clientX - d.pr.left));
        d.curY = Math.max(0, Math.min(d.pr.height, e.clientY - d.pr.top));
        const box = d.kind === "add" ? d.draft : d.marquee;
        box.style.left = `${Math.min(d.startX, d.curX)}px`;
        box.style.top = `${Math.min(d.startY, d.curY)}px`;
        box.style.width = `${Math.abs(d.curX - d.startX)}px`;
        box.style.height = `${Math.abs(d.curY - d.startY)}px`;
      } else if (d.kind === "move") {
        let dx = e.clientX - d.startX, dy = e.clientY - d.startY;
        const snapped = (!e.altKey && d.els.length) ? this.#snapMove(d, dx, dy) : { dx, dy, gx: null, gy: null };
        d.dx = snapped.dx; d.dy = snapped.dy;
        for (const m of d.els) {
          m.el.style.left = `${m.orig.left + d.dx}px`;
          m.el.style.top = `${m.orig.top + d.dy}px`;
        }
        this.#showGuides(d.pageEl, snapped.gx, snapped.gy);
      } else if (d.kind === "resize") {
        let dx = e.clientX - d.startX, dy = e.clientY - d.startY;
        const snapped = !e.altKey ? this.#snapResize(d, dx, dy) : { dx, dy, gx: null, gy: null };
        d.dx = snapped.dx; d.dy = snapped.dy;
        const r = this.#resizedBox(d.orig, d.handle, d.dx, d.dy);
        d.fieldEl.style.left = `${r.left}px`;
        d.fieldEl.style.top = `${r.top}px`;
        d.fieldEl.style.width = `${r.width}px`;
        d.fieldEl.style.height = `${r.height}px`;
        this.#showGuides(d.pageEl, snapped.gx, snapped.gy);
      }
    });

    window.addEventListener("pointerup", () => {
      const d = this._formDrag;
      if (!d) return;
      this._formDrag = null;
      this.#showGuides(d.pageEl, null, null);
      if (d.kind === "add") {
        d.draft.remove();
        const w = Math.abs(d.curX - d.startX), h = Math.abs(d.curY - d.startY);
        let rect;
        if (w >= 10 && h >= 10) {
          rect = pdfRectFromView(d.view.viewport, Math.min(d.startX, d.curX), Math.min(d.startY, d.curY),
            Math.max(d.startX, d.curX), Math.max(d.startY, d.curY));
        } else {
          const [ptW, ptH] = DEFAULT_SIZE[d.tool] || [144, 22];
          const [cx, cy] = d.view.viewport.convertToPdfPoint(d.curX, d.curY);
          rect = clampRect([cx, cy - ptH, cx + ptW, cy], d.view.viewport.viewBox);
        }
        const spec = specForTool(d.tool, d.page, rect);
        this._setFormTool("select");
        this._addFormFields([spec]);
      } else if (d.kind === "marquee") {
        d.marquee.remove();
        const x0 = Math.min(d.startX, d.curX), y0 = Math.min(d.startY, d.curY);
        const x1 = Math.max(d.startX, d.curX), y1 = Math.max(d.startY, d.curY);
        if (!d.additive) this.formPicked = new Set();
        if (x1 - x0 >= 4 || y1 - y0 >= 4) {
          for (const box of d.pageEl.querySelectorAll(".rd-formfield")) {
            const bl = parseFloat(box.style.left), bt = parseFloat(box.style.top);
            const br = bl + parseFloat(box.style.width), bb = bt + parseFloat(box.style.height);
            if (br >= x0 && bl <= x1 && bb >= y0 && bt <= y1) {
              this.formPicked.add(fieldKey(d.page, Number(box.dataset.index)));
            }
          }
        }
        this._drawFormOverlays();
      } else if (d.kind === "move") {
        if (Math.hypot(d.dx, d.dy) < 2) {
          if (d.narrowTo) { this.formPicked = new Set([d.narrowTo]); this._drawFormOverlays(); }
          return;
        }
        const changes = d.els.map((m) => {
          const l = m.orig.left + d.dx, t = m.orig.top + d.dy;
          const rect = pdfRectFromView(d.view.viewport, l, t, l + m.orig.width, t + m.orig.height);
          return { page: d.page, index: m.field.index, rect };
        });
        this._editFormFields(changes, { keepNames: d.els.map((m) => m.field.name) });
      } else if (d.kind === "resize") {
        if (Math.hypot(d.dx, d.dy) < 2) return;
        const r = this.#resizedBox(d.orig, d.handle, d.dx, d.dy);
        const rect = pdfRectFromView(d.view.viewport, r.left, r.top, r.left + r.width, r.top + r.height);
        this._editFormFields([{ page: d.page, index: d.field.index, rect }], { keepNames: [d.field.name] });
      }
    });
  }

  #formGuides(page, view, excludeIdx) {
    const xs = [], ys = [];
    for (const f of (this._formFields()?.[String(page)] || [])) {
      if (excludeIdx.has(f.index)) continue;
      const r = viewRectFromPdf(view.viewport, f.rect);
      xs.push(r.left, r.left + r.width / 2, r.left + r.width);
      ys.push(r.top, r.top + r.height / 2, r.top + r.height);
    }
    const geom = this._geometry?.(page);
    if (geom?.segments) {
      const s = geom.segments;
      const limit = Math.min(geom.count || 0, 2000);
      for (let i = 0; i < limit; i++) {
        if (geom.visible && !geom.visible(i)) continue;
        const x1 = s[i * 4], y1 = s[i * 4 + 1], x2 = s[i * 4 + 2], y2 = s[i * 4 + 3];
        if (Math.abs(x1 - x2) < 0.5 && Math.abs(y1 - y2) > 4) {
          const [vx] = view.viewport.convertToViewportPoint(x1, y1);
          xs.push(vx);
        } else if (Math.abs(y1 - y2) < 0.5 && Math.abs(x1 - x2) > 4) {
          const [, vy] = view.viewport.convertToViewportPoint(x1, y1);
          ys.push(vy);
        }
      }
    }
    return { x: xs, y: ys };
  }

  #nearestGuide(vals, targets, tol = 6) {
    let best = null, bestDelta = tol + 1;
    for (const v of vals) {
      for (const t of targets) {
        const d = t - v;
        if (Math.abs(d) < Math.abs(bestDelta)) { bestDelta = d; best = t; }
      }
    }
    return Math.abs(bestDelta) <= tol ? { delta: bestDelta, pos: best } : { delta: 0, pos: null };
  }

  #snapMove(d, dx, dy) {
    const b = d.els[0].orig;
    let l = b.left + dx, t = b.top + dy, r = l + b.width, bt = t + b.height;
    for (let i = 1; i < d.els.length; i++) {
      const o = d.els[i].orig;
      l = Math.min(l, o.left + dx); t = Math.min(t, o.top + dy);
      r = Math.max(r, o.left + o.width + dx); bt = Math.max(bt, o.top + o.height + dy);
    }
    const sx = this.#nearestGuide([l, (l + r) / 2, r], d.guides.x);
    const sy = this.#nearestGuide([t, (t + bt) / 2, bt], d.guides.y);
    return { dx: dx + sx.delta, dy: dy + sy.delta, gx: sx.pos, gy: sy.pos };
  }

  #snapResize(d, dx, dy) {
    const r = this.#resizedBox(d.orig, d.handle, dx, dy);
    let sx = { delta: 0, pos: null }, sy = { delta: 0, pos: null };
    if (d.handle.includes("w")) sx = this.#nearestGuide([r.left], d.guides.x);
    else if (d.handle.includes("e")) sx = this.#nearestGuide([r.left + r.width], d.guides.x);
    if (d.handle.includes("n")) sy = this.#nearestGuide([r.top], d.guides.y);
    else if (d.handle.includes("s")) sy = this.#nearestGuide([r.top + r.height], d.guides.y);
    return { dx: dx + sx.delta, dy: dy + sy.delta, gx: sx.pos, gy: sy.pos };
  }

  #showGuides(pageEl, gx, gy) {
    if (!pageEl) return;
    const layer = pageEl.querySelector(".rd-formlayer");
    if (!layer) return;
    for (const old of layer.querySelectorAll(".rd-formguide")) old.remove();
    if (gx !== null) layer.append(el("div", { class: "rd-formguide x", style: `left:${gx}px` }));
    if (gy !== null) layer.append(el("div", { class: "rd-formguide y", style: `top:${gy}px` }));
  }

  #resizedBox(orig, handle, dx, dy) {
    let left = orig.left, top = orig.top, right = orig.left + orig.width, bottom = orig.top + orig.height;
    if (handle.includes("w")) left = Math.min(right - 8, left + dx);
    if (handle.includes("e")) right = Math.max(left + 8, right + dx);
    if (handle.includes("n")) top = Math.min(bottom - 8, top + dy);
    if (handle.includes("s")) bottom = Math.max(top + 8, bottom + dy);
    return { left, top, width: right - left, height: bottom - top };
  }

  _addFormFields(specs) {
    if (!specs?.length || !this.doc) return;
    this._formFlushNudge();
    return this._changeDocument((bytes, tab) => this._post("/api/form/add", bytes, tab, { fields: JSON.stringify(specs) }), {
      failure: "Couldn't add form fields: ",
      edited: (res, tab) => {
        tab.formFields = null;
        if (tab.formHint !== "has-fields") { tab.formHint = "has-fields"; this.onFormHint?.(tab); }
        let names = [];
        try { names = JSON.parse(res.headers.get("X-Pdfm-Names") || "[]"); } catch (e) {}
        this._formPickByNames = names;
      },
      after: () => this._selectFieldsByName(this._formPickByNames),
    });
  }

  editFormFields(changes, opts = {}) { return this._editFormFields(changes, opts); }

  copyFieldToPages(page, index, pages, sameName = true) {
    if (!this.doc) return;
    return this._changeDocument((bytes, tab) => this._post("/api/form/copy", bytes, tab, {
      page: String(page), index: String(index), pages: String(pages), same_name: sameName ? "1" : "0",
    }), {
      failure: "Couldn't copy field to pages: ",
      edited: (_res, tab) => { tab.formFields = null; },
      after: () => this._drawFormOverlays(),
    });
  }

  makeFieldTable(page, index, rows, cols, rowGap = 4, colGap = 4) {
    if (!this.doc) return;
    return this._changeDocument((bytes, tab) => this._post("/api/form/table", bytes, tab, {
      page: String(page), index: String(index), rows: String(rows), cols: String(cols),
      row_gap: String(rowGap), col_gap: String(colGap),
    }), {
      failure: "Couldn't make table of fields: ",
      edited: (res, tab) => {
        tab.formFields = null;
        let names = [];
        try { names = JSON.parse(res.headers.get("X-Pdfm-Names") || "[]"); } catch (e) {}
        this._formPickByNames = names;
      },
      after: () => this._selectFieldsByName(this._formPickByNames),
    });
  }

  _editFormFields(changes, { keepNames = null, keepSelection = false } = {}) {
    if (!changes?.length || !this.doc) return;
    const savedKeys = keepSelection ? new Set(this.formPicked) : null;
    return this._changeDocument((bytes, tab) => this._post("/api/form/edit", bytes, tab, { changes: JSON.stringify(changes) }), {
      failure: "Couldn't update form fields: ",
      edited: (_res, tab) => {
        tab.formFields = null;
        this._formPickByNames = keepNames;
      },
      after: () => {
        if (keepNames) this._selectFieldsByName(this._formPickByNames);
        else if (savedKeys) { this.formPicked = savedKeys; this._drawFormOverlays(); }
        else { this.formPicked = new Set(); this._drawFormOverlays(); }
      },
    });
  }

  async _selectFieldsByName(names) {
    if (!names?.length || !this.formMode) return;
    const set = new Set(names);
    const pages = this._formFields();
    if (!pages) {
      // Wait briefly for the new version's fields to arrive; _drawFormOverlays will pick them up.
      const tab = this.activeTab;
      if (tab) {
        try {
          const res = await this._post("/api/form/fields", await this._currentBytes(), tab, { details: "1" });
          if (res.ok && tab === this.activeTab) {
            const { fields } = await res.json();
            tab.formFields = { version: tab.version || 0, pages: fields || {} };
          }
        } catch (e) { /* ignored */ }
      }
    }
    const loaded = this.activeTab?.formFields?.pages || {};
    const picked = new Set();
    for (const [pStr, list] of Object.entries(loaded)) {
      for (const f of list) if (set.has(f.name)) picked.add(fieldKey(Number(pStr), f.index));
    }
    this.formPicked = picked;
    this._drawFormOverlays();
  }

  _deleteSelectedFields() {
    this._formFlushNudge();
    const sel = this._selectedFields();
    if (!sel.length) return;
    this._editFormFields(sel.map((f) => ({ page: f.page, index: f.index, delete: true })));
  }

  _duplicateSelectedFields() {
    this._formFlushNudge();
    const sel = this._selectedFields();
    if (!sel.length) return;
    const specs = sel.map((f) => {
      const view = this.pdfViewer.getPageView(f.page - 1);
      const rect = clampRect([f.rect[0] + 12, f.rect[1] - 12, f.rect[2] + 12, f.rect[3] - 12], view?.viewport?.viewBox);
      return specForTool(f.type, f.page, rect, f);
    });
    this._addFormFields(specs);
  }

  _copySelectedFields() {
    const sel = this._selectedFields();
    if (!sel.length) return false;
    this.formClipboard = sel.map((f) => ({ ...f, rect: [...f.rect], options: [...(f.options || [])] }));
    this.toast(`Copied ${sel.length} field${sel.length === 1 ? "" : "s"}`, 1500);
    return true;
  }

  _pasteFields() {
    if (!this.formClipboard?.length || !this.doc) return false;
    const page = this.pdfViewer.currentPageNumber || 1;
    const view = this.pdfViewer.getPageView(page - 1);
    const bounds = view?.viewport?.viewBox;
    const specs = this.formClipboard.map((f) => {
      const samePage = f.page === page;
      const dx = samePage ? 12 : 0, dy = samePage ? -12 : 0;
      const rect = clampRect([f.rect[0] + dx, f.rect[1] + dy, f.rect[2] + dx, f.rect[3] + dy], bounds);
      return specForTool(f.type, page, rect, f);
    });
    this._addFormFields(specs);
    return true;
  }

  _nudgeSelectedFields(dx, dy) {
    const sel = this._selectedFields();
    if (!sel.length) return false;
    if (!this._formNudge) this._formNudge = { dx: 0, dy: 0, items: sel, timer: null };
    this._formNudge.dx += dx;
    this._formNudge.dy += dy;
    const ndx = this._formNudge.dx, ndy = this._formNudge.dy;
    for (const f of this._formNudge.items) {
      const view = this.pdfViewer.getPageView(f.page - 1);
      const box = this.viewerEl.querySelector(`.rd-formfield[data-page="${f.page}"][data-index="${f.index}"]`);
      if (view?.viewport && box) {
        const r = viewRectFromPdf(view.viewport, [f.rect[0] + ndx, f.rect[1] + ndy, f.rect[2] + ndx, f.rect[3] + ndy]);
        box.style.left = `${r.left}px`;
        box.style.top = `${r.top}px`;
      }
    }
    clearTimeout(this._formNudge.timer);
    this._formNudge.timer = setTimeout(() => this._formFlushNudge(), NUDGE_REST);
    return true;
  }

  _formFlushNudge() {
    const n = this._formNudge;
    if (!n) return;
    this._formNudge = null;
    clearTimeout(n.timer);
    if (!n.dx && !n.dy) return;
    const changes = n.items.map((f) => ({
      page: f.page, index: f.index,
      rect: [f.rect[0] + n.dx, f.rect[1] + n.dy, f.rect[2] + n.dx, f.rect[3] + n.dy],
    }));
    this._editFormFields(changes, { keepNames: n.items.map((f) => f.name) });
  }

  _formKeyDown(e) {
    if (!this.formMode || !this.doc || isTyping(e)) return false;
    if (this.formPreview) {
      if (e.key === "Escape") { e.preventDefault(); this.setFormPreview(false); return true; }
      return false;
    }
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === "Escape") {
      e.preventDefault();
      if (this.formTool !== "select") this._setFormTool("select");
      else if (this.formPicked.size) { this.formPicked = new Set(); this._drawFormOverlays(); }
      else this.closeFormMode();
      return true;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && this.formPicked.size) {
      e.preventDefault(); this._deleteSelectedFields(); return true;
    }
    if (mod && e.key.toLowerCase() === "d" && this.formPicked.size) {
      e.preventDefault(); this._duplicateSelectedFields(); return true;
    }
    if (mod && e.key.toLowerCase() === "c" && this.formPicked.size && !String(window.getSelection() || "")) {
      e.preventDefault(); return this._copySelectedFields();
    }
    if (mod && e.key.toLowerCase() === "v" && this.formClipboard?.length) {
      e.preventDefault(); return this._pasteFields();
    }
    const step = e.shiftKey ? 10 : 1;
    if (e.key === "ArrowLeft") { e.preventDefault(); return this._nudgeSelectedFields(-step, 0); }
    if (e.key === "ArrowRight") { e.preventDefault(); return this._nudgeSelectedFields(step, 0); }
    if (e.key === "ArrowUp") { e.preventDefault(); return this._nudgeSelectedFields(0, step); }
    if (e.key === "ArrowDown") { e.preventDefault(); return this._nudgeSelectedFields(0, -step); }
    return false;
  }
};
