/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// The reader's document mutations, undo/redo, saving, redaction-save,
// annotation change tracking and printing. A part of Reader (see ReaderSave).

import * as files from "./files.js";
import { el, pdfjs, svg } from "./readerkit.js";
import { saveAction } from "./tabstate.js";
import { bounded, WHOLE_MS } from "./reader-render.js";

/** Reader's saving, editing history and printing, as a class it extends. */
export const ReaderSave = (Base) => class extends Base {
  /** The view (page, zoom, scroll), to come back to after reloading. */
  _view() {
    return { page: this.pdfViewer.currentPageNumber, scale: this.pdfViewer.currentScaleValue,
      top: this.container.scrollTop, left: this.container.scrollLeft };
  }

  _post(url, bytes, tab, fields = {}, files = []) {
    const body = new FormData();
    body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
    body.append("password", tab.password || "");
    for (const [k, v] of Object.entries(fields)) body.append(k, v);
    for (const f of files) body.append("insert", f, f.name || "insert.pdf");
    return fetch(url, { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
  }

  /** Replace the open document with what ``make(bytes, tab)`` gets back from
   *  the server, keeping the old bytes for Undo. In the queue: a save or a tab
   *  switch asked for meanwhile waits for it. */
  _changeDocument(make, { failure = "Could not change the document: ", goto = null, after = null, signing = false, kind = "page", edited = null } = {}) {
    return this._serial(async () => {
      const tab = this.activeTab;
      if (!tab || !this.doc) return;
      // Changes are made by rewriting the file, which a signature can't survive.
      if (!signing && tab.signatures?.length && !tab.signedWarned) {
        if (await this.ask("This document is signed", "Changing it makes its signatures show as broken: they no longer cover what the document says.",
          [["cancel", "Cancel"], ["change", "Change it anyway", true]]) !== "change") return;
        tab.signedWarned = true;
      }
      this._busy(true);
      try {
        const bytes = await this._currentBytes();
        const res = await make(bytes, tab);
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
        const changed = new Uint8Array(await res.arrayBuffer());
        if (!this.tabs.includes(tab)) return;
        if (kind === "redact") tab.redactionsApplied(bytes, changed);
        else if (kind === "text") tab.textEdited(bytes, changed);
        else tab.pageEdited(bytes, changed);
        edited?.(res, tab);   // (before it is shown again: what the answer says about the new document)
        if (tab === this.activeTab) { tab.aim(goto ? goto(this._view()) : this._view()); await this._reloadEditing(tab); }
        after?.(res);
      } catch (e) {
        this.toast(failure + (e.message || e));
      } finally {
        this._busy(false);
      }
    });
  }

  _applyTextEdit(edit) {
    return this._changeDocument((bytes, tab) => this._post("/api/text/edit", bytes, tab, { edits: JSON.stringify([edit]) }), {
      failure: "Could not change the text: ",
      kind: "text",
      after: (res) => {
        // Say when the words couldn't get the document's own font.
        let used = [];
        try { used = JSON.parse(res.headers.get("X-Pdfm-Fonts") || "[]"); } catch (e) {}
        const other = used.find((u) => u.kind !== "same" && !u.asked);
        const notes = this._fontNotes(used);
        if (other) {
          notes.push(other.kind === "installed"
            ? `This PDF's copy of the font lacks some of those letters, so ${other.font} from this computer was used.`
            : `This PDF's copy of the font lacks some of those letters, so a similar font (${other.font}) was used.`);
        }
        if (used.some((u) => u.wider)) notes.push("The line is now longer than its column: to wrap it again, rewrite the whole paragraph (Shift+click it).");
        if (used.some((u) => u.taller)) notes.push("The paragraph now takes more lines than before, so it may run into what's below it. Undo with Ctrl+Z.");
        if (used.some((u) => u.wrapped_picture)) notes.push("Some lines were shortened to wrap around a picture.");
        if (used.some((u) => u.next_page)) notes.push("Overflow lines continued onto the next page.");
        if (used.some((u) => u.added_page)) notes.push("Added a new page for the overflow lines.");
        if (used.some((u) => u.overflow) && !edit.fit) {
          notes.push("The text no longer fits on the page, so the text below it wasn't moved.");
          const actions = [["Shrink to fit", () => this._shrinkToFit(edit)]];
          if (used.some((u) => u.can_next_page)) {
            actions.unshift(["Continue on the next page", () => this._fitOverflow(edit, "next_page")]);
          } else if (used.some((u) => u.add_page)) {
            actions.unshift(["Add a page", () => this._fitOverflow(edit, "add_page")]);
          }
          return this.toast(notes.join(" "), 12000, actions);
        }
        if (used.some((u) => u.overflow)) notes.push("Even a little smaller, the text doesn't fit on the page, so the text below it wasn't moved.");
        let summary = null, visual = [];
        try { summary = JSON.parse(res.headers.get("X-Pdfm-Summary") || "null"); } catch (e) {}
        try { visual = JSON.parse(res.headers.get("X-Pdfm-Visual") || "[]"); } catch (e) {}
        if (summary && summary.covered > 0) {
          notes.push("Note: underlying text could not be permanently removed and was visually covered instead.");
        }
        if (Array.isArray(visual) && visual.some((v) => v && v.ok === false)) {
          notes.push("Visual check flagged possible text clipping or overlap — inspect the page or undo with Ctrl+Z.");
        }
        if (notes.length) this.toast(notes.join(" "), 8000);
      },
    });
  }

  _undoTextEdit() {
    return this._serial(async () => {
      const tab = this.activeTab;
      if (!tab?.undoDepth) return;
      // Undoing goes back to the bytes before the change; anything added
      // since (drawings, form entries) would go with it.
      if ((this._changed() || tab.bytes !== tab.afterEdit)
          && await this.ask("Undo the change?", "Undoing it also takes away the annotations and form entries made since it.",
            [["cancel", "Cancel"], ["undo", "Undo the change", true]]) !== "undo") return;
      tab.editUndone(this._view());
      await this._reloadEditing(tab);
      this.toast("Change undone", 1500);
    });
  }

  /** Redo of the last page or text change Undo took back (a note, too). */
  _redoTextEdit() {
    return this._serial(async () => {
      const tab = this.activeTab;
      if (!tab?.redoDepth) return;
      if (this._changed()
          && await this.ask("Redo the change?", "Redoing it takes away the annotations and form entries made since it was undone.",
            [["cancel", "Cancel"], ["redo", "Redo the change", true]]) !== "redo") return;
      tab.editRedone(this._view());
      await this._reloadEditing(tab);
      this.toast("Change redone", 1500);
    });
  }

  /** Which Undo (``redo``: Redo) applies now, the same for the buttons and
   *  the keys: "annot" (pdf.js's history of annotations), "page" (the
   *  reader's own page and text changes, notes and shapes included) or null.
   *  In Edit text and the reader's own markup tools its changes come first,
   *  else pdf.js's. */
  _undoKind(redo = false) {
    const page = !!(redo ? this.activeTab?.redoDepth : this.activeTab?.undoDepth);
    const annot = !!this.uiManager && !!(redo ? this.annotRedo : this.annotUndo);
    if (this.textEditMode || this.markupMode || this.formMode) return page ? "page" : annot ? "annot" : null;
    return annot ? "annot" : page ? "page" : null;
  }

  /** Undo (``redo``: Redo) whichever applies; false if there was nothing. */
  _undo(redo = false) {
    const kind = this._undoKind(redo);
    if (kind === "annot") { if (redo) this.uiManager.redo(); else this.uiManager.undo(); }
    else if (kind === "page") { if (redo) this._redoTextEdit(); else this._undoTextEdit(); }
    return !!kind;
  }

  /** The annotate bar's Undo and Redo: on while there's something for them. */
  _undoButtons() {
    if (!this.undoBtn) return;
    this.undoBtn.disabled = !this._undoKind();
    this.redoBtn.disabled = !this._undoKind(true);
    this._formUndoButtons();
  }

  /** Show the tab's changed bytes, staying in the Edit text tool if it was on. */
  async _reloadEditing(tab) {
    const editing = this.textEditMode ? "EDITTEXT" : this.markupMode, picked = this.pickedImage;
    const wasForm = this.formMode, formTool = this.formTool;
    const wasCheckOcr = this.checkOcrMode;
    const nextOcr = this._ocrNextAfterReload;
    this._ocrNextAfterReload = null;
    const wasSidebar = wasForm && !this.sidebar.hidden;
    const wasFields = wasForm && this.fieldsTab?.getAttribute("aria-selected") === "true";
    await this._load(tab);
    this._refresh();
    if (tab !== this.activeTab) return;
    if (wasCheckOcr) {
      await this.openCheckOcr(null, { autoOpen: false });
      if (this.checkOcrMode) {
        const list = this._allCheckOcrWords();
        const found = nextOcr && list.find((it) => it.page === nextOcr.page && it.word.id === nextOcr.id);
        const target = found || list[0] || null;
        if (target) this._focusCheckOcrWord(target.page, target.word);
      }
    } else if (wasForm) {
      this.openFormMode(formTool);
      if (wasSidebar) this._setSidebar(true);
      if (wasFields) this._showTab("fields");
    } else if (editing) {
      this.openAnnotate();
      this._setEditMode(editing);
      if (editing === "IMAGES" && picked) this.pickedImage = picked;  // the picture just moved stays picked
    }
    this._undoButtons();
  }
  _reloadFormState() { return this.activeTab ? this._reloadEditing(this.activeTab) : Promise.resolve(); }

  // ---- saving, printing, editing ------------------------------------------------
  /** Filled-in form fields or annotations that aren't in the file yet. */
  _changed() { return !!(this.doc && this.doc.annotationStorage.size > 0 && this._annotPrint()); }
  _edited() { return this._changed() || !!this.activeTab?.edited; }

  async _currentBytes() {
    this._finishEditing();
    if (!this._changed()) return await this._fullBytes();
    return await this._savedDocument(this.activeTab);
  }

  /** The shown document's bytes; a file read in pieces is read in full now
   *  (and rejects, saying why, when that can't be done). */
  async _fullBytes() {
    if (!this.bytes && this.doc) {
      const tab = this.activeTab;
      if (tab?.url) await this._readAll(tab);
      else if (tab && !tab.bytes) tab.readInFull(await bounded(this.doc.getData(), WHOLE_MS, "reading the document took too long"));
      if (tab === this.activeTab) this.bytes = tab.bytes;
    }
    return this.bytes;
  }

  _copyName() {
    const stem = this.name.replace(/\.pdf$/i, "");
    return this._edited() ? `${stem} (edited).pdf` : `${stem}.pdf`;
  }

  /** Save the changes into the file itself when it was opened from this
   *  computer (Ctrl+S); otherwise as a copy. */
  async save({ replace = false } = {}) {
    this.noteCommit?.();  // a note typed but not added yet goes in first
    this._markFlush();   // (a markup's arrow presses not written yet go in first)
    await this.chain;  // a text change still on its way is included
    this._finishEditing();  // a stroke or note still being made counts (Ctrl+S straight after drawing)
    const tab = this.activeTab;
    if (!this.doc || !tab) return;
    // What Save does follows from the tab's state (see saveAction): a tab with
    // blacked-out parts saves a redacted copy, not over the original (and
    // not again when one is saved and nothing changed since).
    const action = saveAction(tab, { changed: this._changed(), replace });
    if (action === "already redacted") { this.toast("Already saved as a redacted copy", 2000); return; }
    if (action === "redacted copy") { await this.saveRedacted(); return; }
    if (action === "as") { await this.saveCopy(); return; }
    if (action === "nothing") { this.toast("No changes to save", 1500); return; }
    const version = tab.version;
    let bytes;
    try { bytes = await this._currentBytes(); } catch (e) { this.toast("Could not save: " + (e.message || e), 9000, ["Save as…", () => this.saveCopy()]); return; }
    const put = (force) => fetch("/api/save/" + encodeURIComponent(tab.fileId), { method: "POST", body: bytes,
      headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/pdf", "X-Pdfm-Stamp": tab.stamp || "",
        ...(force ? { "X-Pdfm-Force": "1" } : {}) } });
    try {
      let res = await put(false);
      if (res.status === 409) {
        // Cancel does nothing; the copy is a choice of its own.
        const choice = await this.ask(`“${tab.name}” was changed by another program`,
          "It changed since you opened it. Replace it with your version, or keep both by saving yours as a copy.",
          [["cancel", "Cancel"], ["copy", "Save as a copy"], ["replace", "Replace file", true]]);
        if (choice === "copy") { await this.saveCopy(); return; }
        if (choice !== "replace") return;
        res = await put(true);
      }
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error || res.statusText);
      // The file now holds these bytes (and a file read in pieces is in memory now).
      this._saved(tab, "in place", { version, bytes, stamp: answer.stamp, keep: tab === this.activeTab && !this.bytes });
      // What the file held is kept for a while (not for big files): Undo puts it back.
      if (answer.undo) this.toast(`Saved over ${tab.name}`, 10000, ["Undo", () => this._unsave(tab, answer.undo)]);
      else this.toast(`Saved over ${tab.name}`, 4000);
    } catch (e) {
      this.toast("Could not save: " + (e.message || e), 9000, ["Save as…", () => this.saveCopy()]);
    }
  }

  /** Undo a save over the file: it gets back what it held; the tab keeps
   *  the changes, unsaved again. */
  async _unsave(tab, undo) {
    this.toastHold?.(); this.toastHold = null;
    this.toastEl?.remove();
    try {
      const res = await fetch("/api/unsave", { method: "POST", body: JSON.stringify({ undo }),
        headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/json" } });
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.error || res.statusText);
      if (this.tabs.includes(tab)) tab.saveUndone(answer.stamp);
      this.toast(`Undone: ${tab.name} is as it was before. Your changes are still here, not saved.`, 6000);
    } catch (e) {
      this.toast("Could not undo the save: " + (e.message || e), 9000);
    }
  }

  /** A question in the app's own dialog (never the browser's confirm()).
   *  ``buttons``: [value, label, main?]; resolves with the value of the one
   *  pressed. Escape and Cancel give "cancel", and focus goes back to where it was. */
  ask(title, text, buttons) {
    return this._dialog(title, [text].flat().filter(Boolean).map((t) => el("p", {}, t)), buttons);
  }

  /** Show a file the app has open or saved in its folder (the server checks). */
  async reveal(path) {
    try {
      const res = await fetch("/api/reveal", { method: "POST", body: JSON.stringify({ path }),
        headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/json" } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    } catch (e) { this.toast("Could not show it in its folder: " + (e.message || e), 6000); }
  }

  /** Save an open tab (e.g. a tool's result) as a file, the dialog starting
   *  in the folder of the opened file ``near`` (its id). Resolves with the
   *  path when it's known (the desktop window), true when saved elsewhere, null if not saved. */
  /** Ctrl+S, or Shift for Save as. A tab with blacked-out parts saves a
   *  redacted copy (see saveRedacted), never over its original unasked. */
  _saveKey(e) {
    if (e.shiftKey) this.saveCopy();
    else this.save();
  }

  /** "<name> (redacted).pdf" for a tab with blacked-out parts. */
  _redactedName(tab = this.activeTab) {
    return (tab?.name || "document.pdf").replace(/(?: \(redacted\))?(\.pdf)?$/i, "") + " (redacted).pdf";
  }

  /** Save what's shown, blacked-out parts and all, as a copy beside the
   *  original: "<name> (redacted).pdf", the Save dialog starting in the
   *  original's folder (a download in a plain browser). */
  async saveRedacted() {
    this.noteCommit?.();
    this._markFlush();   // (a markup's arrow presses not written yet go in first)
    await this.chain;
    this._finishEditing();
    if (!this.doc) return;
    const tab = this.activeTab, version = tab.version;
    let bytes;
    try { bytes = await this._currentBytes(); } catch (e) { this.toast("Could not save the redacted copy: " + (e.message || e), 9000); return; }
    await this._saveBytes(bytes, this._redactedName(tab), () => this._saved(tab, "redacted", { version }), tab?.fileId);
  }

  /** Write the redacted version over the original file, once asked. */
  async replaceWithRedacted() {
    const tab = this.activeTab;
    if (!tab?.fileId) { await this.saveRedacted(); return; }
    const answer = await this.ask(`Replace ${tab.name} with the redacted version?`, "The original text can’t be recovered from it.",
      [["cancel", "Cancel"], ["replace", "Replace original", true]]);
    if (answer !== "replace" || this.activeTab !== tab) return;
    await this.save({ replace: true });
  }

  async saveTab(tab, near = null) {
    this._markFlush();   // (a markup's arrow presses not written yet go in first)
    await this.chain;
    if (!tab || !this.tabs.includes(tab)) return null;
    const version = tab.version;
    let bytes;
    try { bytes = tab === this.activeTab ? await this._currentBytes() : tab.bytes || tab.original; }
    catch (e) { this.toast("Could not save: " + (e.message || e), 9000); return null; }
    if (!bytes) return null;
    let done = null;
    const name = /\.pdf$/i.test(tab.name) ? tab.name : tab.name + ".pdf";
    await this._saveBytes(bytes, name, (where) => { done = where || true; this._saved(tab, "as", { version, made: true }); }, near);
    return done;
  }

  /** Save as a new file, starting in the document's own folder (Ctrl+Shift+S). */
  async saveCopy() {
    this._markFlush();   // (a markup's arrow presses not written yet go in first)
    await this.chain;  // a text change still on its way is included
    this._finishEditing();
    if (!this.doc) return;
    const tab = this.activeTab, version = tab.version;
    let bytes;
    try { bytes = await this._currentBytes(); } catch (e) { this.toast("Could not save a copy: " + (e.message || e), 9000); return; }
    await this._saveBytes(bytes, this._copyName(), () => this._saved(tab, "as", { version }), tab?.fileId);
  }

  /** Save ``bytes`` as a file the person picks (or download it); the
   *  dialog starts next to the opened file ``near`` (its id). */
  async _saveBytes(bytes, name, saved = () => {}, near = null) {
    const api = this.api();
    if (this.isDesktop() && api && api.save_result) {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), name);
      try {
        const res = await fetch("/api/keep", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
        const { id } = await res.json();
        const path = await api.save_result(id, name, near);
        if (path) { saved(path); this.toast("Saved to " + path, 5000, ["Show in folder", () => this.reveal(path)]); }
      } catch (e) { this.toast("Could not save: " + (e.message || e)); }
      return;
    }
    const blob = new Blob([bytes], { type: "application/pdf" });
    if (files.canSave) {
      try {
        const where = await files.saveAs(blob, name);
        if (where) { saved(); this.toast("Saved as " + where); }
        return;
      } catch (e) {
        if (e.name !== "SecurityError") { this.toast("Could not save: " + (e.message || e)); return; }
        // not called from a click (e.g. Ctrl+S in some browsers): download instead
      }
    }
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    saved();
  }


  /** Commit the annotation being drawn or typed. */
  _finishEditing() {
    try { this.uiManager?.endCurrentEditing?.(); } catch (e) { /* nothing being edited */ }
  }

  /** ``tab``'s changes up to ``version`` are in a file now (see TabState.saved;
   *  ``how``: "in place", "as" or "redacted"). pdf.js's undo history outlives
   *  the save: Save goes quiet until the next change, and Undo back to here
   *  is unchanged again. */
  _saved(tab, how, details) {
    if (!tab || !this.tabs.includes(tab)) return;
    const here = tab === this.activeTab && this.doc;
    const current = tab.saved(how, { ...details, doc: here ? this.doc : null, print: here ? this._annotPrint() : "" });
    try { if (current && here) this.doc.annotationStorage.resetModified(); } catch (e) { /* (so the next change is noticed) */ }
  }

  /** What the annotations and form entries not in the file yet are, as
   *  text: the same text, the same changes. (Each editor as it would be
   *  saved, without its picture's pixels.) */
  _annotPrint(doc = this.doc) {
    const parts = [];
    for (const [key, value] of doc?.annotationStorage || []) {
      let said = value;
      if (typeof value?.serialize === "function") { try { said = value.serialize(false); } catch (e) { said = "?"; } }
      if (said) parts.push(key + ":" + JSON.stringify(said));
    }
    return parts.join("\n");
  }

  /** pdf.js's storage says it was changed. Turning on one of pdf.js's tools
   *  makes it take the page's own text boxes, drawings and highlights into
   *  its storage, as they are, to be able to edit them: that alone is not a
   *  change (nothing of it would be saved), so the document stays as opened
   *  or saved, and the next real change is still noticed. */
  _storageChanged(tab, doc) {
    const clean = tab.clean;
    if (clean && clean.doc === doc && this._annotPrint(doc) === clean.print) {
      try { doc.annotationStorage.resetModified(); } catch (e) { /* (so the next change is noticed) */ }
      return;
    }
    tab.annotationChanged();
  }

  /** pdf.js's Undo and Redo don't say when the document is back as it was
   *  opened or last saved. It is when the shown document is the one it was
   *  then (no page or text change since: those load the document again)
   *  and its annotations and entries are the same: then it isn't changed
   *  (no dot, Save quiet). Anything else than that state is a change
   *  (see TabState.annotationsAre). */
  _compareAnnotations(tab = this.activeTab) {
    if (!tab || tab !== this.activeTab || !this.doc || tab.clean?.doc !== this.doc) return;
    if (tab.annotationsAre(this.doc, this._annotPrint()) === "saved") {
      try { this.doc.annotationStorage.resetModified(); } catch (e) { /* (so the next change is noticed) */ }
    }
  }

  print() {
    // (Another tab shown while the pages are prepared: nothing is printed.)
    return this._print().catch((e) => { if (!e?.closed) throw e; });
  }

  async _print() {
    if (!this.doc) return;
    const doc = this.doc, total = doc.numPages;
    if (total > 300 && await this.ask(`Print all ${total} pages?`, "Preparing them can take a while.",
      [["cancel", "Cancel"], ["print", "Prepare to print", true]]) !== "print") return;
    let box = document.getElementById("rd-print");
    if (box) box.remove();
    box = el("div", { id: "rd-print" });
    document.body.append(box);
    // The print dialog starts in the document's orientation (a landscape
    // sheet on portrait paper prints small).
    const first = (await doc.getPage(1)).getViewport({ scale: 1 });
    const orientation = el("style", { id: "rd-print-page" }, `@media print { @page { size: ${first.width > first.height ? "landscape" : "portrait"}; margin: 0; } }`);
    document.getElementById("rd-print-page")?.remove();
    document.head.append(orientation);
    const layers = this._printLayers();  // the layers shown (undefined: the document has none)
    try {
      for (let n = 1; n <= total; n++) {
        this.toast(`Preparing page ${n} of ${total} for printing…`, 60000);
        const page = await doc.getPage(n);
        const viewport = page.getViewport({ scale: 150 / 72 });  // 150 dpi
        const canvas = el("canvas", { width: Math.ceil(viewport.width), height: Math.ceil(viewport.height) });
        await page.render({ canvas, viewport, intent: "print", annotationMode: pdfjs.AnnotationMode.ENABLE_STORAGE,
          printAnnotationStorage: doc.annotationStorage.print, optionalContentConfigPromise: layers }).promise;

        const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
        box.append(el("div", { class: "rd-print-page" }, el("img", { src: URL.createObjectURL(blob), alt: "" })));
        if (doc !== this.doc) return;
      }
      if (this.toastEl) this.toastEl.remove();
      await Promise.all([...box.querySelectorAll("img")].map((img) => img.decode().catch(() => {})));
      document.body.classList.add("rd-printing");
      window.print();
    } finally {
      setTimeout(() => {
        document.body.classList.remove("rd-printing");
        for (const img of box.querySelectorAll("img")) URL.revokeObjectURL(img.src);
        box.remove();
        orientation.remove();
      }, 1000);
    }
  }

};
