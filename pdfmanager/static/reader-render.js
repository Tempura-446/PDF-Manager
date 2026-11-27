/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// The reader's document loading, progressive fetching, pdf.js viewer setup,
// form field wiring, signature checks and revision compare.
// A part of Reader (see ReaderRender below).

import { BASE, DOC_OPTIONS, destroyTask, openDocument } from "./pdfjs.js";
import * as files from "./files.js";
import { Overlay } from "./overlay.js";
import { el, ICON, pdfjs, store, svg, viewer, loadPdfjs } from "./readerkit.js";

/** pdf.js's form fields ({name: [field...]}, a Map since pdf.js 5) as [name, list] pairs. */
function fieldEntries(fields) {
  return fields instanceof Map ? [...fields.entries()] : Object.entries(fields || {});
}


export const BROKEN_WHY = "ZPDF can’t show this file — it may be damaged.";
const HIGHLIGHT_COLORS = "yellow=#FFFF98,green=#53FFBC,blue=#80EBFF,pink=#FFCBE6,red=#FF4F5F";

// A file opened from this computer comes in pieces: the first this long (a
// file no bigger is whole in it, and is held here in full), the rest as pages
// are shown (the whole file only when a change or save needs it).
const FIRST_BYTES = 4 * 1024 * 1024;
// A file read in pieces up to this size is read in full in the background
// once its first page is on screen, and then held like a small file: it can
// be shown again, saved elsewhere or compared even if the file on disk is
// changed or deleted meanwhile. Bigger ones stay in pieces.
const HOLD_BYTES = 64 * 1024 * 1024;
// How long the reader waits, at most, for a piece of a file, for all of it,
// and for pdf.js to open a document (not counting the password prompt): a
// request that never answers mustn't leave the reader waiting for ever.
export const WHOLE_MS = 180000;
const PIECE_MS = 60000, OPEN_MS = 120000;
// pdf.js's words for a file it can't make sense of (damaged, or not a PDF).
const DAMAGED = new Set(["InvalidPDFException", "FormatError", "UnknownErrorException", "MissingPDFException"]);

/** fetch(), given up after ``ms`` (and when ``signal`` aborts). */
function fetchFor(url, options, ms, signal = null) {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(new DOMException("no answer in time", "TimeoutError")), ms);
  const onAbort = () => stop.abort(signal.reason);
  if (signal) { if (signal.aborted) onAbort(); else signal.addEventListener("abort", onAbort, { once: true }); }
  // (no-store: the browser lets only one request at a time for an address
  // through its cache, and pieces of one file must go side by side)
  return fetch(url, { cache: "no-store", ...options, signal: stop.signal }).finally(() => {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  });
}

export /** ``promise``, or an error saying ``what`` once ``ms`` have gone by. */
function bounded(promise, ms, what) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(what)), ms); })])
    .finally(() => clearTimeout(timer));
}

/** Make a document that's being closed answer every question with a refusal
 *  (an error with ``closed``). Once pdf.js has torn down a document's link to
 *  its worker, its methods throw ("Cannot read properties of null (reading
 *  'sendWithPromise')") instead of rejecting, and a ``.catch()`` doesn't see
 *  that. pdf.js's own viewer can still be asking the document things (for a
 *  document over 250 pages it hands the page views their pages a batch at a
 *  time, waiting in between, without looking again whether the document is
 *  still shown), and so can work of the reader's still on its way: they all
 *  get a rejection now, which they handle. (All of these methods return
 *  promises.) */
function closeOff(doc) {
  const closed = () => Promise.reject(Object.assign(new Error("the document was closed"), { closed: true }));
  for (let proto = Object.getPrototypeOf(doc); proto && proto !== Object.prototype; proto = Object.getPrototypeOf(proto)) {
    for (const name of Object.getOwnPropertyNames(proto)) {
      if (/^(get|has|save|extract)[A-Z]/.test(name) && typeof Object.getOwnPropertyDescriptor(proto, name).value === "function") {
        Object.defineProperty(doc, name, { value: closed, configurable: true, writable: true });
      }
    }
  }
}

/** Why a piece of a file couldn't be had, from the server's answer; ``gone``
 *  when it won't come back by asking again (the file was moved, deleted or
 *  rewritten). */
async function pieceError(res, tab) {
  if (res.status === 404) return Object.assign(new Error("the file is no longer where it was (moved or deleted)"), { gone: true });
  const stamp = res.headers.get("X-Pdfm-Stamp");
  if (res.ok && stamp && tab.stamp && stamp !== tab.stamp) {
    return Object.assign(new Error("another program changed the file since it was opened here"), { gone: true });
  }
  if (res.status !== 206) return new Error((await res.json().catch(() => ({}))).error || res.statusText || `error ${res.status}`);
  return null;
}
const header = (res, name) => { try { return decodeURIComponent(res.headers.get(name) || ""); } catch (e) { return ""; } };


/** Reader's loading, viewer lifecycle, forms and signatures, as a class it extends. */
export const ReaderRender = (Base) => class extends Base {
  // ---- opening ------------------------------------------------------------------
  async chooseFile() {
    const api = this.api();
    if (this.isDesktop() && api && api.open_pdf) {
      try {
        const picked = await api.open_pdf();
        if (picked) await this.openById(picked.id);
      } catch (e) { this.toast(String(e.message || e)); }
      return;
    }
    if (files.canOpen) {
      // The browser's Open dialog gives a handle, so the file can be reopened from Recent.
      try {
        const [picked] = await files.openPdfs(false);
        if (picked) { files.rememberHandle(picked.handle); await this.openFile(picked.file); }
      } catch (e) { this.fileInput.click(); }
      return;
    }
    this.fileInput.click();
  }

  // ---- recent files ---------------------------------------------------------------
  /** Under "Open a PDF": the app's Recent list, the same rows as on its start
   *  screen (files opened by path, with their facts, and by browser handle). */
  async refreshRecent() {
    if (!this.recent) { this.recentBox.hidden = true; return; }
    let count = 0;
    try { count = await this.recent(this.recentList); } catch (e) { count = 0; }
    this.recentBox.hidden = !count || !!this.doc;
  }

  /** Open a file the server knows by id; ``goto`` = {page, find} shows a
   *  page and searches for text on it (e.g. from the folder search). */
  async openById(id, goto = null, first = null) {
    this._busy(true);
    this.opening = (this.opening || 0) + 1;
    try {
      const url = "/api/file/" + encodeURIComponent(id);
      // One request: the file's first piece, which comes with its size, place
      // and version. A small file is whole in it. (``first``: that request,
      // already made by the page while it was starting.)
      const res = await (first || fetch(url, { cache: "no-store", headers: { "X-Pdfm-Token": this.token, Range: `bytes=0-${FIRST_BYTES - 1}` } }));
      if (!res.ok && res.status !== 416) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const head = new Uint8Array(await res.arrayBuffer());
      const name = header(res, "X-Pdfm-Filename") || "document.pdf";
      const file = { fileId: id, path: header(res, "X-Pdfm-Path"), stamp: res.headers.get("X-Pdfm-Stamp") || "",
        size: Number((res.headers.get("Content-Range") || "").split("/")[1]) || head.length };
      if (head.length >= file.size) await this.openBytes(head, name, null, goto, file);
      else await this._serial(() => this._openBytesNow(null, name, null, goto, { ...file, url, head, headCopy: head.slice() }));  // the rest in pieces
    } catch (e) {
      this.toast("Could not open the file: " + (e.message || e));
    } finally {
      this.opening--;
      if (!this.opening && !this.doc && this.active) this.refreshRecent();  // nothing opened after all
      this._busy(false);
    }
  }

  /** ``tab`` holds a file that can't be shown: say so where its pages would
   *  be, with Try to repair it, Remove from Recent and Close. It stays (for
   *  this tab) until the tab is closed. */
  _showBroken(tab) {
    this.activeTab = tab;
    this._busy(false);
    this.empty.hidden = true;
    this.sigbar.hidden = true;  // (the last document's signature banner isn't this file's)
    this.broken.hidden = false;
    this.brokenName.textContent = tab.name;
    this.brokenName.title = tab.path || tab.name;
    this.brokenRepair.hidden = !this.onRepair || (!tab.fileId && !tab.original);
    this._brokenForPeople(tab);
    // Not damaged, perhaps: protected for particular people (asked once per tab).
    if (tab.forPeople === undefined && this.onBrokenWhy) {
      tab.forPeople = null;
      Promise.resolve(this.onBrokenWhy(tab)).then((known) => {
        tab.forPeople = !!known?.for_people;
        if (this.activeTab === tab && tab.broken) this._brokenForPeople(tab);
      }, () => { tab.forPeople = false; });
    }
    this.brokenForget.hidden = !this.onForgetRecent || !tab.path || tab.forgotten;
    this.brokenSaid.textContent = tab.brokenSaid || "";
    this.pageInput.value = "";
    this.pageCount.textContent = "";
    for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
      this.zoomInput, this.zoomMenuBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = true;
    this.zoomInput.value = "";
    this.sidebar.hidden = true;
    this.root.classList.remove("reading");
    this._refresh();
  }

  /** The words and buttons for a file that can't be shown: damaged (Try to
   *  repair it), or protected for particular people (Open with my Digital ID). */
  _brokenForPeople(tab) {
    const people = !!tab.forPeople && !!this.onOpenWithId;
    this.brokenWhy.textContent = people
      ? "This PDF is protected for particular people: it opens only with the Digital ID of one of them, not with a password."
      : BROKEN_WHY;
    this.brokenOpenId.hidden = !people;
    this.broken.classList.toggle("rd-forpeople", people);  // (a lock, not the "something's wrong" sign)
    this.brokenIcon.innerHTML = svg(people ? "lock" : "alert");
    if (people) this.brokenRepair.hidden = true;
  }

  async _openBrokenWithId() {
    const tab = this.activeTab;
    if (!tab?.broken || !this.onOpenWithId || this.brokenOpenId.disabled) return;
    const pfx = el("input", { type: "file", accept: ".pfx,.p12,application/x-pkcs12", "aria-label": "Digital ID file" });
    const password = el("input", { type: "password", autocomplete: "off", "aria-label": "Digital ID password" });
    const answer = await this._dialog("Open with your Digital ID", [
      el("p", {}, "Your Digital ID is a .pfx or .p12 file. If this PDF was protected for you, it opens it; you get an unprotected copy in a new tab, and the protected file stays as it is."),
      el("label", { class: "rd-field" }, "Digital ID file", pfx), el("label", { class: "rd-field" }, "Its password", password)],
      [["cancel", "Cancel"], ["ok", "Open", true]],
      { ready: () => { if (pfx.files[0]) return true; pfx.focus(); return false; } });
    if (answer !== "ok" || !pfx.files[0]) return;
    this.brokenOpenId.disabled = true;
    this.brokenOpenId.classList.add("busy");
    tab.say("");
    this.brokenSaid.textContent = "";
    try {
      const said = await this.onOpenWithId(tab, pfx.files[0], password.value);
      if (said) { tab.say(said); if (this.activeTab === tab) this.brokenSaid.textContent = said; }
    } finally {
      this.brokenOpenId.disabled = false;
      this.brokenOpenId.classList.remove("busy");
    }
  }

  async _repairBroken() {
    const tab = this.activeTab;
    if (!tab?.broken || !this.onRepair || this.brokenRepair.disabled) return;
    this.brokenRepair.disabled = true;
    this.brokenRepair.classList.add("busy");
    this.brokenRepair.lastChild.textContent = "Repairing…";
    tab.say("");
    this.brokenSaid.textContent = "";
    try {
      const said = await this.onRepair(tab);
      if (said) { tab.say(said); if (this.activeTab === tab) this.brokenSaid.textContent = said; }
    } finally {
      this.brokenRepair.disabled = false;
      this.brokenRepair.classList.remove("busy");
      this.brokenRepair.lastChild.textContent = "Try to repair it";
    }
  }

  async _forgetBroken() {
    const tab = this.activeTab;
    if (!tab?.broken || !this.onForgetRecent) return;
    try { await this.onForgetRecent(tab); } catch (e) { this.toast("Couldn't remove it from Recent: " + (e.message || e)); return; }
    tab.forgot();
    this.brokenForget.hidden = true;
    tab.say(`Removed ${tab.name} from Recent.`);
    this.brokenSaid.textContent = tab.brokenSaid;
    this.brokenClose.focus();
  }

  _showEmpty() {
    this.activeTab = null;
    this._busy(false);
    this.broken.hidden = true;
    this.empty.hidden = false;
    this.pageInput.value = "";
    this.pageCount.textContent = "";
    for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
      this.zoomInput, this.zoomMenuBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = true;
    this.zoomInput.value = "";
    this.sidebar.hidden = true;
    if (this.splitOpen) this.closeSplitView();
    this.root.classList.remove("reading");
    setTimeout(() => { if (!this.doc && !this.opening) this.refreshRecent(); }, 0);  // (see show())
    this._refresh();
  }

  /** The first piece of a file that is read in pieces (``tab.url``), asked
   *  for again when its tab is shown after another one (the piece that came
   *  when the file was opened went to pdf.js). The file must still be the
   *  one that was opened: a file changed or deleted meanwhile is said so,
   *  never shown in its place. */
  async _firstPiece(tab) {
    if (tab.gone) throw Object.assign(new Error(tab.gone), { gone: true });
    let res;
    try {
      res = await fetchFor(tab.url, { headers: { "X-Pdfm-Token": this.token, Range: `bytes=0-${FIRST_BYTES - 1}` } }, PIECE_MS);
    } catch (e) { throw new Error("the file couldn't be read: " + (e.message || e)); }
    const bad = await pieceError(res, tab);
    const size = Number((res.headers.get("Content-Range") || "").split("/")[1]) || 0;
    if (bad || size !== tab.size) {
      const e = bad || Object.assign(new Error("another program changed the file since it was opened here"), { gone: true });
      if (e.gone) tab.fileGone(e.message);
      throw e;
    }
    tab.firstPiece(new Uint8Array(await res.arrayBuffer()));
  }

  /** Where pdf.js gets the pieces of a file that isn't held here in full:
   *  it starts with the first piece (``tab.head``, which it takes) and asks
   *  for the others as pages need them; once all of the file is here
   *  (_readAll), they come from memory. A piece that fails is asked for once
   *  more; if it fails again, ``failed`` rejects (#load gives up opening)
   *  and, once open, the reader says so once and offers to close the tab.
   *
   *  pdf.js has no way to be told a piece can't be had: it would wait for
   *  it for ever, and so would closing the document. So a piece given up
   *  (or still on its way when the document is closed: ``giveUp``) is
   *  answered with empty bytes, which pdf.js reads as nothing to draw; the
   *  document is then ``poisoned``: it is never written out (#savedDocument). */
  _pieces(tab) {
    const source = new pdfjs.PDFDataRangeTransport(tab.size, tab.head);
    const stop = new AbortController();
    const pending = new Map();  // begin -> end of the pieces pdf.js waits for
    source.failed = new Promise((_, reject) => { source.fail = reject; });
    source.failed.catch(() => {});
    source.serve = (data) => {  // all of the file is here
      for (const [begin, end] of pending) source.onDataRange(begin, data.slice(begin, end));
      pending.clear();
    };
    source.giveUp = () => {
      source.poisoned = true;
      stop.abort();
      for (const [begin, end] of pending) source.onDataRange(begin, new Uint8Array(end - begin));
      pending.clear();
    };
    const ask = async (begin, end, again) => {
      try {
        const res = await fetchFor(tab.url, { headers: { "X-Pdfm-Token": this.token, Range: `bytes=${begin}-${end - 1}` } }, PIECE_MS, stop.signal);
        const bad = await pieceError(res, tab);
        if (bad) throw bad;
        const chunk = new Uint8Array(await res.arrayBuffer());
        if (chunk.length !== end - begin) throw new Error("the file is shorter than it was");
        if (pending.delete(begin)) source.onDataRange(begin, chunk);
      } catch (e) {
        if (stop.signal.aborted || !pending.has(begin)) return;
        if (source.data) { source.serve(source.data); return; }
        if (again && !e.gone) { setTimeout(() => ask(begin, end, false), 300); return; }
        if (e.gone) tab.fileGone(e.message);
        if (source.failure) { source.giveUp(); return; }
        source.failure = String(e.message || e);
        source.fail(new Error(source.failure));
        source.giveUp();
        if (source.task && source.task === this.task && this.doc && this.activeTab === tab) {
          this.toast(`Couldn't read more of “${tab.name}”: ${source.failure}. Close it and open it again.`, 15000,
            ["Close", () => this.closeTab(tab)]);
        }
      }
    };
    source.requestDataRange = (begin, end) => {
      pending.set(begin, end);
      if (source.data) source.serve(source.data);
      else if (source.poisoned) source.giveUp();
      else ask(begin, end, true);
    };
    source.abort = () => { stop.abort(); pending.clear(); };
    source.waiting = () => pending.size > 0;
    tab.piecesFrom(source);  // (pdf.js takes the first piece)
    return source;
  }

  /** All of a file read in pieces, read now (once; at most HOLD_BYTES of it
   *  in the background, any size when a change or a save needs it). From
   *  then on the tab holds it as it holds a small file. Rejects, saying why,
   *  if the file can't be read or is no longer the one that was opened. */
  _readAll(tab) {
    if (tab.bytes) return Promise.resolve(tab.bytes);
    if (tab.gone) return Promise.reject(new Error(tab.gone));
    if (!tab.reading) tab.readingAll((async () => {
      const from = tab.headCopy?.length || 0;
      let res;
      try {
        res = await fetchFor(tab.url, { headers: { "X-Pdfm-Token": this.token, Range: `bytes=${from}-${tab.size - 1}` } }, WHOLE_MS);
      } catch (e) { throw new Error("the file couldn't be read: " + (e.message || e)); }
      const bad = await pieceError(res, tab);
      if (bad) { if (bad.gone) tab.fileGone(bad.message); throw bad; }
      let rest;
      try { rest = new Uint8Array(await res.arrayBuffer()); } catch (e) { throw new Error("the file couldn't be read: " + (e.message || e)); }
      if (from + rest.length !== tab.size) throw new Error("the file couldn't be read in full");
      const all = new Uint8Array(tab.size);
      if (from) all.set(tab.headCopy);
      all.set(rest, from);
      if (tab.source) { tab.source.data = all; tab.source.serve(all); }
      const had = tab.bytes;
      tab.readInFull(all);
      if (!had && tab === this.activeTab && !this.bytes) this.bytes = all;
      return tab.bytes;
    })());
    const reading = tab.reading;
    reading.catch(() => { if (tab.reading === reading) tab.readingAll(null); });
    return reading;
  }

  /** The shown document with its unsaved annotations and form entries. */
  async _savedDocument(tab) {
    if (this.pieces?.poisoned) throw new Error(`part of the file couldn't be read (${this.pieces.failure || "it stopped answering"}); close it and open it again`);
    if (tab?.url && !tab.bytes) await this._readAll(tab);  // (pdf.js needs every piece to write it out)
    return await bounded(this.doc.saveDocument(), WHOLE_MS, "writing the document took too long");
  }

  /** Show ``tab``'s document in the viewer. Returns false if it couldn't be
   *  opened (not a PDF, or the password prompt was cancelled). */
  async _load(tab) {
    if (tab.broken) {  // (known not to open: its state again, not another try)
      await this._closeDocument();
      this._showBroken(tab);
      return true;
    }
    this._busy(true);
    let pieces = null, timer = 0, tooLong = null;
    const giveUp = new Promise((_, reject) => { tooLong = reject; });
    giveUp.catch(() => {});
    const arm = () => { clearTimeout(timer); timer = setTimeout(() => tooLong(new Error("opening it took too long")), OPEN_MS); };
    try {
      await loadPdfjs();
      this._ensureViewer();
      await this._closeDocument();
      if (!tab.bytes && !tab.head) await this._firstPiece(tab);
      this.bytes = tab.bytes;
      this.name = tab.name;
      this.password = tab.password;
      pieces = this.pieces = tab.bytes ? null : this._pieces(tab);
      const task = await openDocument({
        ...DOC_OPTIONS,
        ...(tab.bytes
          ? { data: tab.bytes.slice() }  // pdf.js takes ownership of the buffer; keep our copy intact
          : { range: pieces, rangeChunkSize: 1 << 20, disableAutoFetch: true, disableStream: true }),
        password: tab.password || undefined,
        enableXfa: true,
        fontExtraProperties: true,  // fonts' own names, to find the installed copy when editing text
      });
      if (pieces) pieces.task = task;
      task.onPassword = (update, reason) => {
        clearTimeout(timer);  // (the time at the password prompt doesn't count)
        this._busy(false);
        this._askPassword(reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD).then((pw) => {
          if (pw === null) { destroyTask(task); tooLong(Object.assign(new Error("cancelled"), { name: "PasswordException" })); return; }
          tab.unlocked(pw);
          this.password = pw;
          this._busy(true);
          arm();
          update(pw);
        });
      };
      this.task = task;
      arm();
      // (A piece of the file that can't be had, or no answer at all: pdf.js
      // would wait for ever, so the reader stops waiting.)
      const doc = await Promise.race([task.promise, giveUp, ...(pieces ? [pieces.failed] : [])]);
      // A page tree that counts no pages (or -1), or whose first page isn't
      // there: shown as a damaged file, which Repair mends, not a viewer
      // that fails on its own.
      const unusable = () => Object.assign(new Error("its first page can't be found"), { name: "InvalidPDFException" });
      if (!(doc.numPages >= 1)) throw unusable();
      try { await Promise.race([doc.getPage(1), giveUp]); } catch (e) { if (e && e.message === "opening it took too long") throw e; throw unusable(); }
      clearTimeout(timer);
      this.doc = doc;
      this.activeTab = tab;
      doc.annotationStorage.onSetModified = () => this._storageChanged(tab, doc);
      // (Undo can bring it back to how it was opened or saved: see _compareAnnotations.)
      doc.annotationStorage.onResetModified = () => this._compareAnnotations(tab);
      tab.loaded(doc);
      this.pdfViewer.setDocument(doc);
      this.linkService.setDocument(doc, null);
      // (The viewer hands the document to its form scripting itself once the
      // first page is drawn. Handing it over here too made two at once: the
      // second could ask a document closed meanwhile for its fields, e.g.
      // when a page was turned just after switching tabs.)
      // (The viewer hands the document to the find controller itself once the
      // first page is drawn; doing it here too would reset a search started
      // when the pages appear, e.g. from the folder search.)
      this.empty.hidden = true;
      this.broken.hidden = true;
      for (const c of [this.sidebarBtn, this.prevBtn, this.nextBtn, this.pageInput, this.zoomOutBtn, this.zoomInBtn,
        this.zoomInput, this.zoomMenuBtn, this.findBtn, this.annotateBtn, this.scaleBtn]) c.disabled = false;
      this.pageCount.textContent = `of ${doc.numPages}`;
      this.root.classList.add("reading");
      this._applyPanel();
      this._setSidebar(store.get("pdfm-reader-sidebar", false));
      this.thumbs = null;  // made when the Pages panel shows them (_showThumbs)
      this.pickedPages = new Set();
      this._renderPageBar();
      this._buildOutline();
      this._listComments();
      this._loadMeasures();
      this._loadLayers();
      this.sigbar.hidden = true;
      this.formbar.hidden = true;
      this._whenShown(() => { this._checkSignatures(); this._checkForm(); });
      // The rest of a file read in pieces, as soon as the first page is on
      // screen (not before: it would slow that down). See HOLD_BYTES.
      if (tab.url && !tab.bytes && tab.size <= HOLD_BYTES) this._whenShown(() => this._readAll(tab).catch(() => {}), { now: true });
      if (this.mangaLens?.active) this.mangaLens.close();
      doc.getViewerPreferences?.().then((prefs) => {
        if (this.doc !== doc) return;
        const isR2l = prefs?.Direction === "R2L";
        this.toggleRtlReading(isR2l);
        if (isR2l) this.toast("Right-to-left manga mode enabled (← next, → previous).");
      }).catch(() => {});
      doc.getPageLabels().then((labels) => {
        if (doc !== this.doc || !labels) return;
        // Only use labels that differ from plain page numbers.
        if (labels.some((l, i) => l !== String(i + 1))) { this.labels = labels; this.pdfViewer.setPageLabels(labels); this._updatePage(); }
      }).catch(() => {});
      return true;
    } catch (e) {
      clearTimeout(timer);
      await this._closeDocument();
      const why = pieces?.failure || (e && e.message) || String(e);
      if (tab.gone && tab.shown && this.tabs.includes(tab)) {
        // A tab left in the background whose file went away or changed: say so; it can be closed.
        this.toast(`Can't show “${tab.name}” again: ${tab.gone}.`, 15000, ["Close it", () => this.closeTab(tab)]);
      } else if (pieces?.failure) this.toast(`Could not open “${tab.name}”: ${why}`, 9000);
      else if (!tab.shown && this.tabs.includes(tab) && e && DAMAGED.has(e.name)) {
        // Damaged, or not a PDF at all: a state of its own that stays, with Repair.
        tab.broke();
        if (tab.fileId) {  // Recent says so, and doesn't put it first
          fetch("/api/recent/failed", { method: "POST", headers: { "X-Pdfm-Token": this.token, "Content-Type": "application/json" },
            body: JSON.stringify({ id: tab.fileId }) }).catch(() => {});
        }
        this._showBroken(tab);
        this.brokenRepair.hidden ? this.brokenClose.focus() : this.brokenRepair.focus();
        return true;
      } else if (!(e && e.name === "PasswordException")) {  // not just cancelled at the password prompt
        this.toast(e && e.name === "InvalidPDFException" ? `“${tab.name}” isn't a valid PDF.` : "Could not open the PDF: " + why);
      }
      return false;
    } finally {
      clearTimeout(timer);
      this._busy(false);
    }
  }

  _ensureViewer() {
    if (this.pdfViewer) return;
    const eventBus = new viewer.EventBus();
    this.eventBus = eventBus;
    if (this._syncMobileDock) {
      eventBus.on("pagesinit", this._syncMobileDock);
      eventBus.on("pagechanging", this._syncMobileDock);
    }
    this.linkService = new viewer.PDFLinkService({ eventBus, externalLinkTarget: viewer.LinkTarget.BLANK, externalLinkRel: "noopener noreferrer nofollow" });
    this.findController = new viewer.PDFFindController({ eventBus, linkService: this.linkService });
    // Form scripts (number and date formats, totals, checks — Acrobat's AF
    // functions) run in pdf.js's sandbox, as in Firefox. Nothing else in a
    // PDF is run: the sandbox is handed the document through formScriptsOnly
    // (below), which leaves out its own scripts, its pages' and any field
    // script that isn't made of those functions alone.
    this.scripting = new viewer.PDFScriptingManager({ eventBus, sandboxBundleSrc: BASE + "build/pdf.sandbox.min.mjs",
      wasmUrl: BASE + "wasm/" });
    // The viewer hands the scripting manager each document itself, without
    // waiting for the answer: one closed meanwhile (another tab shown, a
    // change reloading it) would leave a rejection nobody catches.
    // A page's own open and close scripts: the manager reads them from the
    // page the first time it's shown. Told every page was shown before, it
    // never does.
    Object.defineProperty(this.scripting, "_visitedPages", { value: new (class extends Map { has() { return true; } })() });
    const setScripted = this.scripting.setDocument.bind(this.scripting);
    this.scripting.setDocument = (doc) => setScripted(doc ? this._formScriptsOnly(doc) : doc).catch(() => {});
    this.pdfViewer = new viewer.PDFViewer({
      container: this.container, viewer: this.viewerEl, eventBus,
      linkService: this.linkService, findController: this.findController, scriptingManager: this.scripting,
      annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS,
      annotationEditorMode: pdfjs.AnnotationEditorType.NONE,
      annotationEditorHighlightColors: HIGHLIGHT_COLORS,
      imageResourcesPath: BASE + "web/images/",
    });
    this.linkService.setViewer(this.pdfViewer);
    this.scripting.setViewer(this.pdfViewer);
    // The window (or the sidebar) changes size: a zoom that fits the page
    // fits it again, as a reader should. What's watched is the area around
    // the pages, not the scrolling box itself: that one narrows when its
    // scrollbar appears (Windows), which a new fit can bring or take away,
    // and the fitting would go round and round.
    // A bar opening or closing above the pages (find, annotate, scale) only
    // changes the height a little: that's not a reason to zoom.
    let refit = 0, lastW = 0, lastH = 0;
    new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (!width || !height) return;  // hidden (another page of the app is shown): nothing to fit to
      if (Math.abs(width - lastW) < 4 && Math.abs(height - lastH) < 100) return;
      lastW = width;
      lastH = height;
      cancelAnimationFrame(refit);
      refit = requestAnimationFrame(() => {
        const value = this.pdfViewer.currentScaleValue;
        if (this.doc && ["auto", "page-width", "page-fit", "page-height"].includes(value)) this.pdfViewer.currentScaleValue = value;
      });
    }).observe(this.main);
    eventBus.on("annotationeditoruimanager", ({ uiManager }) => { this.uiManager = uiManager; this._applyAnnotParams(); });
    // pdf.js asks to switch tools itself, e.g. after a picture is pasted, or
    // for a click (double-click on one in the file) on an annotation with no
    // tool on: that one is then picked, to move, resize or delete.
    eventBus.on("switchannotationeditormode", ({ mode, editId = null, mustEnterInEditMode = false, editComment = false }) =>
      this._setEditMode(this._modeName(mode), false, editId ? { editId, mustEnterInEditMode, editComment } : null));
    eventBus.on("annotationeditormodechanged", ({ mode }) => this._showEditMode(this._modeName(mode)));
    eventBus.on("editingstateschanged", ({ details }) => {
      this.annotUndo = !!details.hasSomethingToUndo;
      this.annotRedo = !!details.hasSomethingToRedo;
      this._compareAnnotations();
      this._refresh({ notify: false });
      this.deleteBtn.disabled = !details.hasSelectedEditor && !this.markSel;
    });

    eventBus.on("pagesinit", () => {
      const saved = store.get("pdfm-pos:" + this._key(), null);
      this.pdfViewer.currentScaleValue = saved?.scale || "auto";
      if (saved?.page > 1 && saved.page <= this.doc.numPages) this.pdfViewer.currentPageNumber = saved.page;
      if (saved?.rotation) this.pdfViewer.pagesRotation = saved.rotation;
      const goto = this.activeTab?.arrived();
      if (goto) this._goto(goto);
      const tab = this.activeTab, tool = tab?.restored();
      if (tool) {
        // (Once a page's editing layer is drawn: before that, pdf.js's viewer
        // still announces the tool it had, and could leave its own unchanged.)
        let done = false, timer = 0;
        const restore = () => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          eventBus.off("annotationeditorlayerrendered", restore);
          setTimeout(() => {
            if (this.activeTab !== tab || !this.doc || !this.uiManager) return;
            this.openAnnotate();
            if (tool in pdfjs.AnnotationEditorType && tool !== "NONE") this._setEditMode(tool);
          }, 0);
        };
        eventBus.on("annotationeditorlayerrendered", restore);
        timer = setTimeout(restore, 5000);
      }
      this._updatePage();
      this._showThumbs();
      this.container.focus({ preventScroll: true, focusVisible: false });  // (no ring when a file opens)
    });
    // pdf.js makes each page's text a Tab stop; the page view is one already (text still selects).
    eventBus.on("textlayerrendered", ({ source }) => { const layer = source?.textLayer?.div || source?.div?.querySelector(".textLayer"); if (layer) layer.tabIndex = -1; });
    eventBus.on("pagechanging", ({ pageNumber }) => { this._updatePage(); this._remember(); this._refreshScale(); if (this.measureMode) this._geometry(pageNumber); });
    eventBus.on("pagesloaded", () => { this._whenShown(() => this._refreshScale()); this._sizeThumbs(); });
    eventBus.on("annotationlayerrendered", ({ source }) => this._nameFields(source?.div?.querySelector(".annotationLayer")));
    // What's typed into one PDF's form isn't offered in another's: the
    // browser's own memory of form entries is turned off for the fields.
    eventBus.on("annotationlayerrendered", ({ source }) => {
      const layer = source?.div?.querySelector(".annotationLayer");
      for (const field of layer?.querySelectorAll("input, textarea, select") || []) {
        field.setAttribute("autocomplete", "off");
      }
      if (!layer || !source?.pdfPage?.getAnnotations) return;
      source.pdfPage.getAnnotations().then((annots) => {
        if (!layer.isConnected) return;
        const byId = new Map([...layer.children].map((el) => [el.dataset.annotationId, el]));
        for (const a of annots) {
          const sec = byId.get(a.id);
          if (sec) layer.append(sec);
        }
        this._wireFormActions(layer, annots, source.id);
      }).catch(() => {});
    });
    eventBus.on("pagerendered", ({ pageNumber }) => {
      this._drawMeasures(pageNumber);
      if (this.markupMode === "IMAGES" || this.markupMode === "LINKS") this._drawObjectBoxes(pageNumber);
      this._drawRedactions(pageNumber);
      this._markReady();          // (the document may have been written again: its markups, and the selected one's handles)
      this._markDraw(pageNumber);
      if (this.formMode) this._drawFormOverlays();
      if (this.checkOcrMode) this._drawCheckOcrOverlays(pageNumber);
      if (this.overlay) this.overlay.draw(this.pdfViewer.getPageView(pageNumber - 1), pageNumber).catch(() => {});
    });
    eventBus.on("scalechanging", ({ scale, presetValue }) => {
      const value = presetValue || String(scale);
      const preset = ZOOM_PRESETS.find(([v]) => v === value || (!presetValue && Math.abs(parseFloat(v) - scale) < 0.001));
      if (!presetValue && Math.abs(scale - this._trueZoom()) < 0.001) this.zoomPreset = "true-size";
      else this.zoomPreset = preset ? preset[0] : "custom";
      this._showZoom(scale);
      this._syncViewAt();
      this._remember();
      requestAnimationFrame(() => {
        this._drawAllMeasures();
        if (this.formMode) this._drawFormOverlays();
        if (this.checkOcrMode) this._drawCheckOcrOverlays();
      });
    });
    eventBus.on("rotationchanging", () => {
      this._remember();
      requestAnimationFrame(() => {
        this._drawAllMeasures();
        if (this.formMode) this._drawFormOverlays();
        if (this.checkOcrMode) this._drawCheckOcrOverlays();
      });
    });
    eventBus.on("updatefindmatchescount", ({ matchesCount }) => this._findResult(null, matchesCount));
    eventBus.on("updatefindcontrolstate", ({ state, matchesCount }) => this._findResult(state, matchesCount));
  }

  async _closeDocument() {
    this.gaveWay = null;  // (no bar comes back for a document that is going)
    this._putAwayMeasures();  // (its list of measurements goes with it)
    this._closeLayers();
    this.closeFind();
    await this.closeCompare();
    this.closeAnnotate();
    this.closeFormMode();
    this.closeCheckOcr();
    this.measureTool(null);
    this.uiManager = null;
    this.annotUndo = this.annotRedo = false;
    this.labels = null;
    for (const stop of this.asking || []) { stop.closed = true; stop.abort(); }
    this.thumbs = null;
    this.thumbsList.replaceChildren();
    this.outlineList.replaceChildren();
    const doc = this.doc, task = this.task, pieces = this.pieces;
    this.doc = null;
    this.task = null;
    this.pieces = null;
    // A file read in pieces: what's still on its way is given up, and if
    // pdf.js won't let go of the document soon, the reader doesn't wait.
    const wait = pieces && (pieces.poisoned || pieces.waiting()) ? 2000 : 10000;
    pieces?.giveUp();
    this._refresh({ notify: false });
    if (doc) {
      closeOff(doc);
      this.pdfViewer.setDocument(null);
      this.linkService.setDocument(null);
      this.findController.setDocument(null);
      // (The viewer lets go of the form scripting too; it finishes before the
      // document goes, so it never asks a destroyed one for anything.)
      const scripts = this.scripting?.destroyPromise;
      if (scripts) await Promise.race([scripts, new Promise((r) => setTimeout(r, 1500))]).catch(() => {});
      await destroyTask(doc.loadingTask || task, wait);  // pdf.js frees a document through its task
    } else if (task) {
      await destroyTask(task, wait);  // e.g. cancelled at the password prompt: wait for it to be gone
    }
  }

  _key() { return this.doc ? this.doc.fingerprints[0] : ""; }
  _remember() {
    if (!this.doc) return;
    const key = "pdfm-pos:" + this._key();
    const prev = store.get(key, null) || {};
    const page = this.pdfViewer.currentPageNumber || 1;
    const total = this.pdfViewer.pagesCount || this.doc.numPages || 1;
    const maxPage = Math.max(page, Number(prev.maxPage) || 1);
    const percent = Math.min(100, Math.round((page / Math.max(1, total)) * 100));
    store.set(key, {
      page,
      scale: this.pdfViewer.currentScaleValue,
      rotation: this.pdfViewer.pagesRotation,
      maxPage,
      totalPages: total,
      percent,
      updatedAt: Date.now(),
    });
    const fileId = this.activeTab?.fileId;
    if (fileId) {
      clearTimeout(this._progressTimer);
      const payload = {
        file_id: fileId,
        page,
        total,
        zoom: String(this.pdfViewer.currentScaleValue || ""),
        rtl: !!this.rtlReading,
        spread: (this.pdfViewer.spreadMode || 0) > 0,
      };
      this._progressTimer = setTimeout(() => {
        fetch("/api/library/progress", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(this.token ? { "X-Pdfm-Token": this.token } : {}),
          },
          body: JSON.stringify(payload),
        }).catch(() => {});
      }, 600);
    }
  }

  _busy(on) { this.loading.hidden = !on; }


  // ---- digital ID signatures (certsign.py) --------------------------------------------
  /** Sign the document with a digital ID, its signature shown in ``rect`` on page ``n``. */
  async _signWithId(n, rect) {
    const pfx = el("input", { type: "file", accept: ".pfx,.p12,application/x-pkcs12", "aria-label": "Digital ID file" });
    const password = el("input", { type: "password", autocomplete: "off", "aria-label": "Digital ID password" });
    const reason = el("input", { type: "text", placeholder: "e.g. I approve this document", "aria-label": "Reason" });
    const location = el("input", { type: "text", placeholder: "e.g. Vancouver", "aria-label": "Location" });
    const stamp = el("input", { type: "text", placeholder: "none (e.g. http://timestamp.digicert.com)", "aria-label": "Timestamp server",
      value: store.get("pdfm-timestamp-url", "") });
    const answer = await this._dialog("Digital ID", [
      el("p", {}, "Your Digital ID is a .pfx or .p12 file with your certificate. The signature seals the document: a change after signing shows up when it's checked."),
      el("label", { class: "rd-field" }, "Digital ID file", pfx), el("label", { class: "rd-field" }, "Its password", password),
      el("label", { class: "rd-field" }, "Reason (optional)", reason), el("label", { class: "rd-field" }, "Location (optional)", location),
      el("label", { class: "rd-field" }, "Timestamp server (optional)", stamp)],
      [["cancel", "Cancel"], ["ok", "Sign", true]],
      { ready: () => { if (pfx.files[0]) return true; pfx.focus(); return false; } });  // (Enter before a digital ID is chosen: to the file box, nothing lost)
    if (answer !== "ok") return;
    const file = pfx.files[0];
    if (!file) { this.toast("Choose your Digital ID file (.pfx or .p12)."); return; }
    store.set("pdfm-timestamp-url", stamp.value.trim());
    const tab = this.activeTab, version = tab?.version || 0;
    await this._changeDocument((bytes, t) => {
      const body = new FormData();
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
      body.append("pfx", file, file.name);
      for (const [k, v] of Object.entries({ pfx_password: password.value, password: t.password || "", page: String(n), box: JSON.stringify(rect),
        reason: reason.value.trim(), location: location.value.trim(), timestamp_url: stamp.value.trim() })) body.append(k, v);
      return fetch("/api/sign/cert", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    }, { failure: "Could not sign: ", signing: true });
    if ((tab?.version || 0) !== version) {
      if (this.markupMode === "CERTSIGN") this._setEditMode("NONE");
      tab.signedWarned = false;
      this.toast("Signed. Save it now: anything changed after this shows as changed after signing.", 7000, ["Save", () => this.save()]);
    }
  }

  /** What the document's signatures say: { signatures: [...] } (see certsign.verify). */
  async _verifySignatures() {
    const tab = this.activeTab;
    if (!tab || !this.doc) return [];
    const body = await this._about(tab);
    if (!body) return [];
    const res = await fetch("/api/sign/verify", { method: "POST", body, headers: { "X-Pdfm-Token": this.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return (await res.json()).signatures || [];
  }

  /** What a request about ``tab``'s document sends: the id of the file on
   *  this computer while that file holds what the tab shows (nothing is
   *  uploaded then), else the document itself. null: nothing to send. */
  async _about(tab, fields = {}) {
    const body = new FormData();
    if (this.isPristine(tab)) body.append("id", tab.fileId);
    else {
      const bytes = tab === this.activeTab ? await this._fullBytes() : tab.bytes;  // (annotations not saved yet don't matter here)
      if (!bytes) return null;
      body.append("file", new Blob([bytes], { type: "application/pdf" }), "file.pdf");
    }
    body.append("password", tab.password || "");
    for (const [k, v] of Object.entries(fields)) body.append(k, v);
    return body;
  }

  /** A question the reader asks the server on its own about the shown
   *  document. It is given up when the document is closed (the error then
   *  has ``closed``) and after ``ms``: nothing waits for an answer for ever. */
  async _ask(url, body, ms = 30000) {
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), ms);
    (this.asking ||= new Set()).add(stop);
    try {
      const res = await fetch(url, { method: "POST", body, headers: { "X-Pdfm-Token": this.token }, signal: stop.signal });
      return { ok: res.ok, data: await res.json().catch(() => ({})) };
    } catch (e) {
      e.closed = !!stop.closed;
      throw e;
    } finally {
      clearTimeout(timer);
      this.asking.delete(stop);
    }
  }

  /** Run ``fn`` once the shown document's first page is on screen and the
   *  page has a quiet moment (not at all if another document is shown by
   *  then): what isn't needed to show the file doesn't hold it up. */
  _whenShown(fn, { now = false } = {}) {
    const doc = this.doc, bus = this.eventBus;
    let timer = 0, done = false;
    const go = () => { if (doc === this.doc) fn(); };
    const shown = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      bus.off("pagerendered", shown);
      if (now) setTimeout(go, 0);  // (``now``: not waiting for a quiet moment)
      else if (window.requestIdleCallback) requestIdleCallback(go, { timeout: 1500 }); else setTimeout(go, 100);
    };
    const view = this.pdfViewer.getPageView((this.pdfViewer.currentPageNumber || 1) - 1);
    if (view?.renderingState === 3) { shown(); return; }  // (it's on screen already)
    bus.on("pagerendered", shown);
    timer = setTimeout(shown, 5000);  // (a first page that takes long to draw, or fails)
  }

  /** Resolves once the shown document's first page is on screen and the page is idle. */
  shown() { return new Promise((resolve) => { if (this.doc) this._whenShown(resolve); else resolve(); }); }

  /** A signed document says so (and whether the signatures hold) in a bar. */
  /** Form fields' names for screen readers: pdf.js leaves its inputs
   *  unnamed (it puts the tooltip on the box around them). */
  async _nameFields(layer) {
    const doc = this.doc;
    if (!doc || !layer) return;
    if (!this.fieldNames || this.fieldNames.doc !== doc) {
      const names = new Map();
      try {
        for (const [name, list] of fieldEntries(await doc.getFieldObjects())) {
          for (const f of list) names.set(f.id, f.type === "radiobutton" && f.exportValues ? `${name}: ${f.exportValues}` : name);
        }
      } catch (e) { /* unnamed then */ }
      this.fieldNames = { doc, names };
    }
    for (const input of layer.querySelectorAll("input, select, textarea")) {
      if (input.getAttribute("aria-label") || input.labels?.length) continue;
      const box = input.closest("[data-annotation-id]");
      const label = box?.title || this.fieldNames.names.get(input.dataset.elementId || box?.dataset.annotationId);
      if (label) input.setAttribute("aria-label", label);
    }
  }

  async _wireFormActions(layer, annots, pageNum) {
    const tab = this.activeTab;
    if (!tab || !this.doc || !layer.isConnected) return;
    const hasInteractive = annots.some((a) => a.pushButton || a.checkBox);
    if (!hasInteractive) return;

    const ver = tab.version || 0;
    let pages = tab.formFields?.version === ver ? tab.formFields.pages : null;
    if (!pages && tab.formActions?.version === ver) pages = tab.formActions.pages;
    if (!pages) {
      try {
        const bytes = await this._fullBytes();
        if (!bytes || tab !== this.activeTab) return;
        const res = await this._post("/api/form/fields", bytes, tab, { details: "1" });
        if (res.ok) {
          ({ fields: pages } = await res.json());
          if ((tab.version || 0) === ver) tab.formActions = { version: ver, pages: pages || {} };
        }
      } catch (e) { /* ignored */ }
    }
    if (!layer.isConnected || tab !== this.activeTab) return;

    const pageList = pages?.[String(pageNum)] || [];
    const byName = new Map(pageList.map((f) => [f.name, f]));

    const applyShowHide = (names, show) => {
      if (!names?.length) return;
      const set = new Set(names);
      for (const el of this.viewerEl.querySelectorAll(".annotationLayer [name]")) {
        if (!set.has(el.getAttribute("name"))) continue;
        const sec = el.closest("section");
        if (sec) sec.hidden = !show;
      }
    };

    for (const a of annots) {
      const sec = layer.querySelector(`[data-annotation-id="${CSS.escape(a.id)}"]`);
      if (!sec) continue;
      const meta = a.fieldName ? byName.get(a.fieldName) : null;

      if (a.checkBox && meta?.show_hide?.length) {
        const input = sec.querySelector('input[type="checkbox"]');
        if (input && !input.dataset.showHideWired) {
          input.dataset.showHideWired = "1";
          const sync = () => applyShowHide(meta.show_hide, input.checked);
          input.addEventListener("change", sync);
          sync();
        }
      }

      if (a.pushButton && !sec.dataset.actionWired) {
        sec.dataset.actionWired = "1";
        let act = meta?.action || null;
        if (!act) {
          if (a.action === "Print") act = { type: "print" };
          else if (a.resetForm) act = { type: "reset", fields: a.resetForm.fields || [] };
          else if (a.url) act = { type: "url", url: a.url };
        }
        if (!act) continue;
        sec.style.cursor = "pointer";
        sec.addEventListener("click", (e) => {
          if (this.formMode && !this.formPreview) return;
          e.preventDefault();
          e.stopPropagation();
          if (act.type === "reset") {
            this._resetFormFields(act.fields || []);
          } else if (act.type === "print") {
            this.print();
          } else if (act.type === "goto" && act.page) {
            this.pdfViewer.currentPageNumber = act.page;
          } else if (act.type === "url" && /^https?:/i.test(act.url || "")) {
            this._lastOpenedUrl = act.url;
            window.open(act.url, "_blank", "noopener,noreferrer");
          } else if (act.type === "email") {
            const q = act.subject ? `?subject=${encodeURIComponent(act.subject)}` : "";
            const mailto = `mailto:${act.to || ""}${q}`;
            this._lastMailto = mailto;
            const link = el("a", { href: mailto, hidden: true });
            document.body.append(link);
            link.click();
            link.remove();
          }
        });
      }
    }
  }

  _resetFormFields(fieldNames = []) {
    if (!this.doc) return;
    const only = fieldNames?.length ? new Set(fieldNames) : null;
    for (const input of this.viewerEl.querySelectorAll(".annotationLayer input, .annotationLayer textarea, .annotationLayer select")) {
      const name = input.getAttribute("name");
      if (only && !only.has(name)) continue;
      if (input.type === "checkbox" || input.type === "radio") {
        input.checked = input.defaultChecked;
      } else {
        input.value = input.defaultValue || "";
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (!only) this.doc.annotationStorage?.resetModified?.();
  }

  /** A printed form with no fields to type in (lines, boxes, tick boxes on
   *  its first pages): offer to make it fillable. */
  async _checkForm() {
    const doc = this.doc, tab = this.activeTab;
    this.formbar.hidden = true;
    if (!doc || !tab || tab.formHint === "hidden" || tab.formHint === "has-fields" || tab.formHint === "checking") return;
    tab.formHint ||= "checking";
    try {
      // (Fields first: also after "Make it fillable" reloads the document.)
      if (fieldEntries(await doc.getFieldObjects()).length) { tab.formHint = "has-fields"; return; }
      if (tab.formHint === "show") { if (doc === this.doc) this._showFormHint(tab); return; }
      // (A document that isn't a file on this computer is sent to be looked at: not a very big one.)
      if (tab.formHint !== "checking" || !this.isPristine(tab) && (!tab.bytes || tab.bytes.length > 30e6)) { tab.formHint = "none"; return; }
      const body = await this._about(tab, { pages: "1-2" });
      const res = body && await this._ask("/api/form/detect", body);
      if (!res?.ok) { tab.formHint = "none"; return; }
      const found = Object.values(res.data.fields || {}).flat();
      tab.formHint = found.length >= 3 ? "show" : "none";
      if (tab.formHint === "show" && doc === this.doc && tab === this.activeTab) this._showFormHint(tab);
      if (tab.formHint === "show") this.onFormHint?.(tab);
    } catch (e) {
      tab.formHint = e.closed ? null : "none";  // closed meanwhile (another tab shown): looked at again when it's back
    }
  }

  _showFormHint(tab) {
    this.formText.textContent = "This looks like a form to fill in, but you can't type in it yet.";
    this.formbar.hidden = false;
  }

  /** Whether ``tab`` looks like a printed form with nothing to type in yet
   *  (what the "Make it fillable" bar is shown for). */
  looksLikeFlatForm(tab = this.activeTab) { return !!tab && tab.formHint === "show"; }

  /** Add the fields of a printed form, so it can be filled in here. */
  makeFillable() {
    if (!this.doc) return;
    this.formbar.hidden = true;
    return this._changeDocument((bytes, tab) => this._post("/api/form/fillable", bytes, tab), {
      failure: "Couldn't make it fillable: ",
      after: (res) => {
        const n = Number(res.headers.get("X-Pdfm-Fields")) || 0;
        if (this.activeTab) { this.activeTab.formHint = "has-fields"; this.onFormHint?.(this.activeTab); }
        this.toast(`Added ${n} place${n === 1 ? "" : "s"} to fill in: click one to type or tick, then save. Add form fields (All tools) can fine-tune them.`);
      },
    });
  }

  async _checkSignatures() {
    const doc = this.doc, tab = this.activeTab;
    this.sigbar.hidden = true;
    if (!doc || !tab) return;
    try {
      // Every signature has a /ByteRange (pdf.js leaves signature fields out
      // of its list of fields): the server looks for one before anything else
      // ("quick"), in the file itself when it's on this computer.
      const body = await this._about(tab, { quick: "1" });
      const res = body && doc === this.doc && await this._ask("/api/sign/verify", body, 120000);
      if (!res?.ok || doc !== this.doc || tab !== this.activeTab) return;
      if (res.data.error) throw new Error(res.data.error);
      const list = res.data.signatures || [];
      if (!list.length) return;
      tab.signatures = list;
      const bad = list.filter((s) => s.status === "modified" || s.status === "invalid");
      const names = [...new Set(list.map((s) => s.signer).filter(Boolean))].join(", ");
      this.sigbar.classList.toggle("bad", bad.length > 0);
      this.sigText.textContent = bad.length
        ? `${bad.length === 1 ? "A signature" : `${bad.length} signatures`} no longer hold: the document was changed after signing, or the signature is broken.`
        : `Signed by ${names || "someone"}${list.length > 1 ? ` (${list.length} signatures)` : ""}: not changed since. `
          + (list.every((s) => s.status === "valid") ? "The signer's identity is verified." : "The signer's identity isn't verified (their certificate isn't one this computer trusts).");
      this.sigbar.hidden = false;
    } catch (e) {
      if (e.closed || e.name === "AbortError" || doc !== this.doc) return;  // closed meanwhile, or no answer
      this.sigText.textContent = "This document is signed, but its signatures couldn't be checked: " + (e.message || e);
      this.sigbar.classList.add("bad");
      this.sigbar.hidden = false;
    }
  }

  async _signatureDetails() {
    let list = this.activeTab?.signatures;
    try { list = await this._verifySignatures(); } catch (e) { this.toast("Couldn't check the signatures: " + (e.message || e)); return; }
    if (!list.length) { this.toast("This document has no signatures."); return; }
    const words = { valid: "Valid: not changed since, and the signer's identity is verified",
      "valid-untrusted": "Valid: not changed since. The signer's identity isn't verified (their certificate isn't one this computer trusts)",
      modified: "Changed after signing", invalid: "Not valid: the signature is broken or doesn't match" };
    const when = (t) => { const d = t && new Date(t); return d && !Number.isNaN(+d) ? d.toLocaleString() : t || ""; };
    const items = list.map((s) => el("li", { class: "rd-sigitem " + (s.status || "") },
      el("b", {}, (s.kind === "timestamp" ? "Document timestamp" : s.signer || "Unknown signer") + (s.email ? ` <${s.email}>` : "")),
      el("div", {}, words[s.status] || s.status || ""),
      s.summary ? el("div", { class: "rd-muted" }, s.summary) : null,
      s.lasting ? el("div", { class: "rd-muted" }, s.lasting) : null,
      el("small", {}, [s.time ? "Signed " + when(s.time) + (s.time_source === "timestamp" ? " (time from a timestamp server)" : " (time from the signer's computer)") : "",
        s.reason ? "Reason: " + s.reason : "", s.location ? "Location: " + s.location : "", s.page ? "Page " + s.page : "",
        s.issuer ? "Certificate from " + s.issuer : ""].filter(Boolean).join(" · "))));
    await this._dialog("Signatures", [el("ul", { class: "rd-siglist" }, ...items)], [["ok", "Close", true]]);
  }

  // ---- comparing revisions (overlay.js) -------------------------------------------
  /** Draw another version of this document over it: what changed shows in red
   *  (only in that version) and blue (only in this one). */
  async compareRevision() {
    if (!this.doc) return;
    const others = this.tabs.filter((t) => t !== this.activeTab);
    const answer = await this._dialog("Compare with another revision", [el("p", {},
      "The other version is drawn over this one, page by page: what both have is black, what's only in that version red, "
      + "and what's only in this one blue. Pick the other version:")],
      [...others.map((t) => ["tab:" + t.id, t.name]), ["file", "Choose a file…"], ["cancel", "Cancel"]]);
    let source = null, name = "", password = null;
    if (answer.startsWith("tab:")) {
      const tab = others.find((t) => "tab:" + t.id === answer);
      if (!tab) return;
      let bytes = tab.bytes;
      if (!bytes) {
        try { bytes = await this._readAll(tab); } catch (e) { this.toast(`Couldn't read “${tab.name}”: ${e.message || e}`, 9000); return; }
      }
      source = { data: bytes.slice() };
      name = tab.name;
      password = tab.password;
    } else if (answer === "file") {
      const file = await this._pickPdf();
      if (!file) return;
      source = { data: new Uint8Array(await file.arrayBuffer()) };
      name = file.name;
    } else return;
    this._busy(true);
    try {
      await this.closeCompare();
      const overlay = new Overlay(openDocument, destroyTask, DOC_OPTIONS);
      await overlay.open(source, password, (wrong) => this._askPassword(wrong));
      this.overlay = overlay;
      overlay.mode = this.overlayMode.value;
      this.overlayOffset.value = "0";
      this.overlayName.textContent = name;
      this.overlaybar.hidden = false;
      await this._drawOverlays();
      if (overlay.doc.numPages !== this.doc.numPages) {
        this.toast(`That version has ${overlay.doc.numPages} pages, this one ${this.doc.numPages}: if sheets moved, set its page number offset in the bar.`, 7000);
      }
    } catch (e) {
      this.toast("Could not open the other version: " + (e.message || e));
    } finally {
      this._busy(false);
    }
  }

  async closeCompare() {
    const overlay = this.overlay;
    if (!overlay) return;
    this.overlay = null;
    this.overlaybar.hidden = true;
    for (let i = 0; i < (this.pdfViewer?.pagesCount || 0); i++) overlay.clear(this.pdfViewer.getPageView(i));
    await overlay.close();
  }

  /** Draw the comparison on the pages drawn so far. */
  async _drawOverlays() {
    const overlay = this.overlay;
    if (!overlay || !this.pdfViewer) return;
    for (let i = 0; i < this.pdfViewer.pagesCount; i++) {
      const view = this.pdfViewer.getPageView(i);
      if (view?.canvas?.width) await overlay.draw(view, i + 1).catch(() => {});
      if (this.overlay !== overlay) return;
    }
  }

};
