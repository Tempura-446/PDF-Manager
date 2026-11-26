/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// The reader's tabs: the open documents (each a TabState, tabstate.js), the
// queue that shows one at a time, switching, closing, and what the screen
// shows from their state (the tab bar, the dots, the Save buttons, the
// title). A part of Reader (see Tabs below).

import { el, svg } from "./readerkit.js";
import { TabState, deriveUi, closePrompt } from "./tabstate.js";

function sameBytes(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
/** Reader's tabs, as a class it extends (see Measuring in
 *  measuring.js): opening a document in a tab (or switching to its tab),
 *  switching (the shown tab's unsaved annotations kept in its bytes, its
 *  tool restored when it comes back), closing (asking first when it would
 *  lose something), the queue every change of document waits in (_serial),
 *  and drawing the tab bar and the save state from the tabs' state (_refresh). */
export const Tabs = (Base) => class extends Base {
  /** Run ``fn`` after the document changes started before it. */
  _serial(fn) {
    // (A markup's arrow presses not written yet are written first: markedit.js.)
    if (this.markNudge) this._markFlush();
    const run = this.chain.then(() => fn());
    this.chain = run.catch(() => {});
    return run;
  }

  async openFile(file, password = null) {
    const name = file.name || "document.pdf";
    if (/\.(dxf|dwg)$/i.test(name)) {
      if (this.loading) this.loading.hidden = false;
      try {
        const res = await fetch("/api/drawing/open", {
          method: "POST",
          headers: { "X-Pdfm-Token": this.token, "X-Pdfm-Filename": encodeURIComponent(name) },
          body: await file.arrayBuffer(),
        });
        if (!res.ok) {
          const msg = (await res.json().catch(() => ({}))).error || res.statusText;
          this.toast(msg);
          return;
        }
        const pdfBytes = new Uint8Array(await res.arrayBuffer());
        await this.openBytes(pdfBytes, name.replace(/\.(dxf|dwg)$/i, ".pdf"), password);
      } finally {
        if (this.loading) this.loading.hidden = true;
      }
      return;
    }
    if (/\.cbz$/i.test(name)) {
      if (this.loading) this.loading.hidden = false;
      try {
        const res = await fetch("/api/cbz/open", {
          method: "POST",
          headers: { "X-Pdfm-Token": this.token, "X-Pdfm-Filename": encodeURIComponent(name) },
          body: await file.arrayBuffer(),
        });
        if (!res.ok) {
          const msg = (await res.json().catch(() => ({}))).error || res.statusText;
          this.toast(msg);
          return;
        }
        const pdfBytes = new Uint8Array(await res.arrayBuffer());
        await this.openBytes(pdfBytes, name.replace(/\.cbz$/i, ".pdf"), password);
      } finally {
        if (this.loading) this.loading.hidden = true;
      }
      return;
    }
    await this.openBytes(new Uint8Array(await file.arrayBuffer()), name, password);
  }

  /** Open a PDF in a new tab, or switch to its tab if it's already open. */
  openBytes(bytes, name, password = null, goto = null, file = null) {
    return this._serial(() => this._openBytesNow(bytes, name, password, goto, file));
  }

  /** A tool's result: a new tab right after ``beside`` (the tab it was made
   *  from, which stays open as it was), shown. Resolves with the new tab, or
   *  null if it couldn't be opened. */
  openResult(bytes, name, password, beside) {
    return this._serial(async () => {
      await this._openBytesNow(bytes, name, password, null, null, beside);
      const tab = this.activeTab;
      if (!tab || tab.name !== name) return null;
      const at = this.tabs.indexOf(beside);  // (one open already, switched to: it moves beside it)
      if (at >= 0 && this.tabs.indexOf(tab) !== at + 1) {
        this.tabs.splice(this.tabs.indexOf(tab), 1);
        this.tabs.splice(at + 1, 0, tab);
        this._refresh();
      }
      const wrap = this.tabEls?.get(tab.id)?.wrap;  // it slides in, once
      wrap?.classList.add("rd-new");
      wrap?.addEventListener("animationend", () => wrap.classList.remove("rd-new"), { once: true });
      return tab;
    });
  }

  /** What the shown tab holds now, its unsaved changes included (for a tool
   *  run on it); the tab itself stays as it is, unsaved changes and all. */
  async currentBytes() {
    await this.chain;
    if (!this.doc) return null;
    return await this._currentBytes();
  }

  /** Whether the file on this computer holds what ``tab`` shows (opened from
   *  there, nothing changed since it was last saved). */
  isPristine(tab = this.activeTab) { return !!(tab && tab.fileId && !tab.dirty); }

  /** Draw the tab bar again (e.g. after the app marked a tab unsaved). */
  refreshTabs() { this._refresh(); }

  /** Switch to an open tab (e.g. back to the one a result was made from). */
  showTab(tab) { return this.#switchTo(tab); }

  /** ``file``: where a document opened from this computer lives (fileId,
   *  path, stamp, size; url when it's read in pieces, bytes then null).
   *  ``beside``: the new tab goes right after that one (else at the end). */
  async _openBytesNow(bytes, name, password, goto, file = null, beside = null) {
    const same = this.tabs.find((t) => (file?.path && t.path === file.path)
      || (bytes && t.original && t.name === name && sameBytes(t.original, bytes))
      || (bytes && !t.original && t.headCopy && t.name === name && t.size === bytes.length  // (read in pieces, not all here yet)
        && sameBytes(t.headCopy, bytes.subarray(0, t.headCopy.length)))
      // Opened by id and read in pieces (only its first piece here) while the same file is open
      // from its bytes (e.g. a tool's file card, then Recent): the same name, size and first piece.
      || (!bytes && file?.head && t.original && t.name === name && t.original.length === file.size
        && sameBytes(t.original.subarray(0, file.head.length), file.head)));
    if (same) {
      if (same === this.activeTab) { if (goto) this._goto(goto); }
      else { if (goto) same.aim(goto); await this.#switchToNow(same); }  // applied once its pages are there
      return;
    }
    const previous = this.activeTab;
    await this.#stash();
    const tab = new TabState({ bytes, name, password, goto, file }, (t, what) => this.#tabChanged(t, what));
    // (In its place from the start: the tab bar shows it while it opens.)
    const at = beside ? this.tabs.indexOf(beside) : -1;
    if (at >= 0) this.tabs.splice(at + 1, 0, tab);
    else this.tabs.push(tab);
    if (!(await this._load(tab))) {
      this.tabs.splice(this.tabs.indexOf(tab), 1);
      tab.closed();
      if (!(previous && this.tabs.includes(previous) && await this._load(previous))) {
        await this._closeDocument();
        this._showEmpty();
      }
    }
    this._refresh();
  }

  /** Keep the open tab's unsaved annotations and form entries in its bytes
   *  before another document takes its place in the viewer. */
  async #stash() {
    const tab = this.activeTab;
    this._finishEditing();
    if (!tab) return;
    // The tool in use comes back with the tab (see pagesinit): with one on,
    // pdf.js makes its annotations, unsaved ones kept in its bytes too,
    // editors again, to pick, move, resize or delete.
    const tool = this.doc && !this.annotbar.hidden ? this.editMode || "NONE" : null;
    tab.stashed({ tool });
    if (!this.doc || !this._changed()) return;
    try {
      tab.stashed({ tool, bytes: await this._savedDocument(tab) });
    } catch (e) {
      this.toast(`Your unsaved changes to “${tab.name}” couldn't be kept: ${e.message || e}`, 9000);
    }
  }

  #switchTo(tab) {
    // (Until it is shown, the viewer still holds the tab before: a turn of
    // the view asked for meanwhile waits for it, see rotate.)
    this.switching = (this.switching || 0) + 1;
    return this._serial(() => this.#switchToNow(tab)).finally(() => { this.switching--; });
  }

  async #switchToNow(tab) {
    if (tab === this.activeTab || !this.tabs.includes(tab)) return;
    const previous = this.activeTab;
    await this.#stash();
    // (A tab that can't be shown: the one shown before comes back.)
    if (!(await this._load(tab)) && !(previous && this.tabs.includes(previous) && await this._load(previous))) {
      await this._closeDocument();
      this._showEmpty();
    }
    this._refresh();
  }

  async closeTab(tab = this.activeTab) {
    const answer = await this._serial(() => this.#closeTabNow(tab));
    if (answer !== "save") return;
    // Save first (as Ctrl+S would; Save as for a tool's result not saved anywhere yet), then close.
    if (tab !== this.activeTab) await this.#switchTo(tab);
    if (tab.unsaved || !tab.fileId) await this.saveTab(tab, tab.madeBy?.from?.fileId || null);
    else await this.save();
    if (this.tabs.includes(tab) && !tab.needsSaving) await this._serial(() => this.#closeTabNow(tab, true));
  }

  /** Close ``tab``; one with changes not saved (or a tool's result not saved
   *  anywhere) asks first. Resolves "save" when Save was chosen: closeTab
   *  saves it, then closes it. */
  async #closeTabNow(tab, saved = false) {
    if (!tab || !this.tabs.includes(tab)) return;
    const prompt = closePrompt(tab);
    if (prompt && !saved) {
      const answer = await this.ask(prompt.title, prompt.text, [["cancel", "Cancel"], ["close", "Close without saving"], ["save", prompt.save, "seal"]]);
      if (answer === "save") return "save";
      if (answer !== "close") return;
    }
    const i = this.tabs.indexOf(tab);
    this.tabs.splice(i, 1);
    tab.closed();
    if (tab === this.activeTab) {
      this.activeTab = null;
      const next = this.tabs[i] || this.tabs[i - 1];
      if (!next || !(await this._load(next))) { await this._closeDocument(); this._showEmpty(); }
    }
    this._refresh();
  }

  /** Any tab that would be lost on closing ZPDF: changes not saved to a
   *  file, or a tool's result not saved anywhere (the tabs with a dot, whose
   *  own close asks too; see deriveUi). */
  hasUnsavedChanges() {
    return deriveUi(null, this.tabs).anyUnsaved;
  }

  /** A tab's state changed (one of its transitions): show what follows.
   *  (The app hears of it too, except for one more change to a tab already
   *  unsaved: nothing it shows changes then.) */
  #tabChanged(tab, what) {
    if (this.tabs.includes(tab)) this._refresh({ notify: what !== "annotation" && what !== "loaded" });
  }

  /** Show what follows from the open tabs' state, all of it in one place:
   *  the tab bar and its dots, the title (and its dot), the Save and Undo
   *  buttons, the window's title (see deriveUi). ``notify``: tell the app
   *  (its file panel follows the shown document). */
  _refresh({ notify = true } = {}) {
    if (!this.saveNowBtn) return;
    this.#drawTabBar();
    const tab = this.activeTab;
    const ui = this.ui = deriveUi(tab, this.tabs, {
      shown: !!this.doc, annotating: !this.annotbar.hidden, annotUndo: !!this.annotUndo, changed: this._changed(),
      // (While areas are marked for Redact, Black out is the main action: Save waits, quiet.)
      marked: this.markupMode === "REDACT" && !!tab && this._redactCount(tab) > 0, active: this.active,
    });
    // A dot (and the word, for screen readers): changes not saved, or a tool's result not saved anywhere yet.
    for (const [id, parts] of this.tabEls) {
      const dot = !!ui.dots.get(id);
      parts.dot.hidden = parts.unsaved.hidden = !dot;
      parts.unsaved.textContent = dot ? ", unsaved" : "";  // (the words too: some tools read hidden text)
    }
    // With one document (no tab bar) the title says it too.
    this.titleDot.hidden = this.titleSr.hidden = !ui.titleDot;
    this.titleText.textContent = ui.heading;
    this.titleEl.title = ui.heading;
    if (ui.title) document.title = ui.title;
    // Save and Undo in the toolbar while there are changes to keep or take
    // back (the annotation bar has its own while it's open); the annotation
    // bar's Save is the screen's main action only once there is something to
    // save (it still works).
    this.saveNowBtn.hidden = !ui.saveNow;
    this.undoNowBtn.hidden = !ui.undoNow;
    this._undoButtons();
    this.annotSaveBtn.classList.toggle("seal", ui.annotSave.main);
    this.annotSaveBtn.classList.toggle("primary", ui.annotSave.main);
    this.annotSaveBtn.title = ui.annotSave.title;
    if (this.formSaveBtn) {
      this.formSaveBtn.classList.toggle("seal", ui.annotSave.main);
      this.formSaveBtn.classList.toggle("primary", ui.annotSave.main);
      this.formSaveBtn.title = ui.annotSave.title;
    }
    // The app keeps the server told what's unsaved: closing the desktop
    // window asks from there (it can't ask the page then).
    const unsaved = JSON.stringify(ui.unsaved);
    if (unsaved !== this.unsavedSaid) { this.unsavedSaid = unsaved; this.onUnsaved?.(ui.unsaved); }
    if (notify) this.onChange?.();
  }

  /** The tab bar. Tabs keep their elements (a click mustn't land on an
   *  element that was replaced halfway through it). */
  #drawTabBar() {
    // (A tab still opening isn't there yet: it comes once it's shown, as the one shown.)
    const tabs = this.tabs.filter((t) => t.shown || t.broken || t === this.activeTab);
    this.tabbar.hidden = tabs.length < 2;
    this.tabEls ||= new Map();
    for (const [id, parts] of this.tabEls) if (!tabs.some((t) => t.id === id)) { parts.wrap.remove(); this.tabEls.delete(id); }
    if (!this.addTabBtn) {
      this.addTabBtn = el("button", { type: "button", class: "rd-btn rd-doctab-add", title: "Open another PDF", "aria-label": "Open another PDF", html: svg("plus") });
      this.addTabBtn.addEventListener("click", () => this.chooseFile());
    }
    const order = tabs.map((tab) => {
      let parts = this.tabEls.get(tab.id);
      if (!parts) {
        const dot = el("span", { class: "rd-dot", title: "Not saved" });
        const label = el("span", { class: "rd-doctab-label" }, tab.name);
        const unsaved = el("span", { class: "rd-sr", hidden: true });
        const btn = el("button", { type: "button", class: "rd-doctab-name", role: "tab", title: tab.name }, dot, label, unsaved);
        btn.addEventListener("click", () => this.#switchTo(tab));
        const close = el("button", { type: "button", class: "rd-doctab-x", title: "Close", "aria-label": `Close ${tab.name}`, html: svg("x") });
        close.addEventListener("click", () => this.closeTab(tab));
        const wrap = el("div", { class: "rd-doctab" }, btn, close);
        wrap.addEventListener("auxclick", (e) => { if (e.button === 1) { e.preventDefault(); this.closeTab(tab); } });
        parts = { wrap, btn, dot, unsaved };
        this.tabEls.set(tab.id, parts);
      }
      const active = tab === this.activeTab;
      parts.wrap.classList.toggle("active", active);
      parts.btn.setAttribute("aria-selected", String(active));
      return parts.wrap;
    });
    // Re-append only if the order changed, so elements stay where they are.
    const current = [...this.tabbar.children].filter((n) => n !== this.addTabBtn);
    if (current.length !== order.length || current.some((n, i) => n !== order[i])) this.tabbar.replaceChildren(...order, this.addTabBtn);
    else if (!this.addTabBtn.isConnected) this.tabbar.append(this.addTabBtn);
  }

  /** Capture current disk-backed tabs + split-view state and open Workspace Sessions modal. */
  async openWorkspaceSessions() {
    let sessions = [];
    try {
      const res = await fetch("/api/sessions");
      if (res.ok) sessions = (await res.json()).sessions || [];
    } catch {
      sessions = [];
    }
    const overlay = el("div", { class: "rd-modal-backdrop", role: "dialog", "aria-modal": "true", "aria-label": "Workspace sessions" });
    Object.assign(overlay.style, {
      position: "fixed", inset: "0", zIndex: "1200", background: "rgb(0 0 0 / .45)",
      display: "grid", placeItems: "center", padding: "16px",
    });
    const card = el("div", { class: "rd-modal-card" });
    Object.assign(card.style, {
      width: "min(520px, 100%)", maxHeight: "80vh", overflowY: "auto",
      background: "var(--surface)", color: "var(--text)", borderRadius: "12px",
      border: "1px solid var(--border)", boxShadow: "var(--shadow-pop)", padding: "18px 20px",
    });
    const close = () => overlay.remove();
    const header = el("div", { style: "display:flex;align-items:center;justify-content:space-between;margin-bottom:12px" },
      el("strong", { style: "font-size:15px" }, "Workspace Sessions"),
      el("button", { type: "button", class: "rd-btn compact", title: "Close", html: svg("x"), onclick: close }));

    const diskTabs = (this.tabs || []).filter((t) => t.fileId);
    const nameInput = el("input", {
      type: "text", class: "rd-input", placeholder: "Session name (e.g. Thesis Review)…",
      style: "flex:1;min-width:0;padding:6px 10px;border:1px solid var(--border-strong);border-radius:6px;background:var(--bg);color:var(--text)",
    });
    const saveBtn = el("button", { type: "button", class: "rd-btn", disabled: !diskTabs.length }, "Save current");
    const listWrap = el("div", { style: "display:flex;flex-direction:column;gap:8px;margin-top:12px" });

    const renderList = (items) => {
      listWrap.replaceChildren();
      if (!items.length) {
        listWrap.append(el("div", { style: "color:var(--muted);font-size:13px;padding:10px 0" }, "No saved workspace sessions yet."));
        return;
      }
      for (const s of items) {
        const avail = (s.tabs || []).filter((t) => !t.missing && t.id).length;
        const total = (s.tabs || []).length;
        const meta = `${avail}/${total} file${total === 1 ? "" : "s"}${s.split?.open ? " · Split view" : ""}`;
        const openBtn = el("button", { type: "button", class: "rd-btn compact", disabled: avail === 0 }, "Restore");
        openBtn.addEventListener("click", async () => { close(); await this.restoreWorkspaceSession(s); });
        const delBtn = el("button", { type: "button", class: "rd-btn compact", title: "Delete session", html: svg("x") });
        delBtn.addEventListener("click", async () => {
          await fetch("/api/sessions/delete", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: s.id }),
          });
          sessions = sessions.filter((x) => x.id !== s.id);
          renderList(sessions);
        });
        const row = el("div", {
          style: "display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg)",
        },
          el("div", { style: "flex:1;min-width:0" },
            el("div", { style: "font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, s.name),
            el("div", { style: "font-size:12px;color:var(--muted)" }, meta)),
          openBtn, delBtn);
        listWrap.append(row);
      }
    };

    saveBtn.addEventListener("click", async () => {
      const name = (nameInput.value || "").trim() || `Workspace ${new Date().toLocaleDateString()}`;
      const tabsPayload = diskTabs.map((t) => {
        const isCurrent = t === this.activeTab && this.pdfViewer;
        return {
          fileId: t.fileId,
          name: t.name,
          page: isCurrent ? (this.pdfViewer.currentPageNumber || 1) : (t.goto?.page || 1),
          scale: isCurrent ? (this.pdfViewer.currentScaleValue || "auto") : (t.goto?.scale || "auto"),
          rotation: isCurrent ? (this.pdfViewer.pagesRotation || 0) : (t.goto?.rotation || 0),
        };
      });
      const activeIdx = Math.max(0, diskTabs.indexOf(this.activeTab));
      let splitPayload = { open: false };
      if (this.splitOpen) {
        const special = (this._splitSourceValue === "__same__" || this._splitSourceValue === "__translate__")
          ? this._splitSourceValue : null;
        const srcIdx = special ? null : diskTabs.findIndex((t) => String(t.id) === String(this._splitSourceValue));
        splitPayload = {
          open: true,
          source_special: special,
          source_index: srcIdx >= 0 ? srcIdx : null,
          ratio: this._splitRatio || 0.5,
          sync: !!this.splitSync?.checked,
          offset: Number(this.splitOffset?.value || 0),
        };
      }
      const res = await fetch("/api/sessions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, tabs: tabsPayload, active_index: activeIdx, split: splitPayload }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        this.toast(err.error || "Could not save workspace session.");
        return;
      }
      nameInput.value = "";
      const fresh = await fetch("/api/sessions").then((r) => (r.ok ? r.json() : { sessions: [] }));
      sessions = fresh.sessions || [];
      renderList(sessions);
      this.toast(`Saved workspace “${name}”.`);
    });

    const formRow = el("div", { style: "display:flex;gap:8px;align-items:center" }, nameInput, saveBtn);
    const hint = !diskTabs.length
      ? el("div", { style: "font-size:12px;color:var(--muted);margin-top:6px" }, "Open one or more PDFs from disk to save a workspace session.")
      : el("span");
    renderList(sessions);
    card.append(header, formRow, hint, listWrap);
    overlay.append(card);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.body.append(overlay);
    nameInput.focus();
  }

  /** Restore a saved workspace session (tabs, reading positions, active tab, and split view). */
  async restoreWorkspaceSession(session) {
    const items = (session?.tabs || []).filter((t) => !t.missing && t.id);
    if (!items.length) {
      this.toast("None of the files in that workspace session are available on disk.");
      return;
    }
    const openedTabs = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      await this.openById(it.id, { page: it.page || 1, scale: it.scale, rotation: it.rotation || 0 }, i === 0 && !this.tabs.length);
      if (this.activeTab) openedTabs.push(this.activeTab);
    }
    const targetIdx = Math.max(0, Math.min(session.active_index || 0, openedTabs.length - 1));
    if (openedTabs[targetIdx] && openedTabs[targetIdx] !== this.activeTab) {
      await this.showTab(openedTabs[targetIdx]);
    }
    if (session.split?.open) {
      this._splitRatio = session.split.ratio || 0.5;
      if (this.splitSync) this.splitSync.checked = !!session.split.sync;
      if (this.splitOffset) this.splitOffset.value = String(session.split.offset || 0);
      let target = session.split.source_special || null;
      if (!target && Number.isInteger(session.split.source_index) && openedTabs[session.split.source_index]) {
        target = String(openedTabs[session.split.source_index].id);
      }
      await this.openSplitView(target || "__same__");
    }
    this.toast(`Restored workspace “${session.name}” (${openedTabs.length} tab${openedTabs.length === 1 ? "" : "s"}).`);
  }
};
