// One open document's state in the reader (a tab), and what the screen shows
// for it.
//
// A tab's state used to be flags set by hand all over reader.js (dirty,
// savedSince, redacted, the bytes kept on a tab switch, the clean snapshot,
// the undo stack...), and the unsaved dot, the Save button, the close prompt
// and Ctrl+S each read them in their own way. Now:
//
//   - TabState owns those fields. They can be read (tab.dirty, tab.version,
//     tab.bytes...) but not assigned: each change is a named transition
//     (annotationChanged, pageEdited, saved, ...), and every transition tells
//     the tab's listener, so the reader draws what follows from it again.
//   - deriveUi() works out, from the shown tab's state and a few facts about
//     the viewer, everything the screen says about saving: the dots, the Save
//     buttons, the title, whether closing asks, and what Ctrl+S does.
//
// What a tab holds besides (its measurements, scales, the form hint, the
// pictures and redaction marks found on its pages, signatures...) is data of
// the reader's features, kept on the tab as plain fields; it isn't part of
// whether the tab is saved.

let tabSeq = 0;
const UNDO_KEPT = 30;  // page and text changes Undo can take back

export class TabState {
  // Which file, and the bytes.
  #id; #name; #password; #fileId; #path; #stamp; #size; #url;
  #original = null;   // what the file holds, as far as the reader knows (null: not all read yet)
  #bytes = null;      // what the tab shows: the original, or changed here (page and text changes, or kept on a switch)
  // Changes.
  #edited = false;    // #bytes holds changes made here
  #dirty = false;     // changes not saved to a file yet
  #savedSince = false;  // saved, and nothing changed since (pdf.js's undo history outlives a save)
  #version = 0;       // counts every change, so a save knows what it included
  #clean = null;      // { doc, print }: the document and its annotations as last opened or saved (see annotationsAre)
  #undo = [];         // page and text changes to take back: [{ bytes, redacted, saved }] (saved: unchanged then, as opened or last saved)
  #redo = [];         // ... and the ones taken back, for Redo: [{ bytes, redacted, saved }] (a new change forgets them)
  #afterEdit = null;  // the bytes the last page or text change made
  #redacted = false;  // parts are blacked out: Save makes a redacted copy, never over the original unasked
  #redactedSaved = false;  // ... and such a copy was saved
  #savedOver = null;  // what its file held before the last save over it ({ original, size }), for Undo of that save
  #unsaved = false;   // a tool's result, only in the app until it's saved somewhere
  #madeBy = null;     // what made it: { op, label, from, sizes, notes, ... }
  // Away and back.
  #tool = null;       // the annotation tool that was on when it was switched away from
  #goto = null;       // where to go once its pages are shown: { page, find, scale, top, left }
  // Reading the file.
  #head = null;       // a file read in pieces: its first piece, for pdf.js (which takes it)
  #headCopy = null;   // ... and a copy of it, until all of the file is here
  #reading = null;    // reading all of it: a promise of the bytes
  #source = null;     // ... and where pdf.js gets the pieces
  #shown = false;     // its pages were on screen once
  #broken = false;    // it can't be shown (damaged, not a PDF)
  #brokenSaid = "";   // what the broken-file screen says about it
  #forgotten = false; // taken off Recent from there
  #gone = "";         // why its file can't be read any more (changed or deleted meanwhile)
  #closed = false;
  #listener;

  /** opened: a document in a new tab. ``file``: where one opened from this
   *  computer lives (fileId, path, stamp, size; url, head and headCopy when
   *  it's read in pieces, bytes then null). ``onChange(tab, what)``: told after
   *  each transition. */
  constructor({ bytes = null, name = "document.pdf", password = null, goto = null, file = null } = {}, onChange = null) {
    this.#id = ++tabSeq;
    this.#original = this.#bytes = bytes;
    this.#name = name;
    this.#password = password;
    this.#goto = goto;
    const f = file || {};
    this.#fileId = f.fileId ?? undefined;
    this.#path = f.path ?? undefined;
    this.#stamp = f.stamp ?? undefined;
    this.#size = f.size ?? undefined;
    this.#url = f.url ?? undefined;
    this.#head = f.head || null;
    this.#headCopy = f.headCopy || null;
    this.#listener = onChange;
  }

  // ---- reading -------------------------------------------------------------------
  get id() { return this.#id; }
  get name() { return this.#name; }
  get password() { return this.#password; }
  get fileId() { return this.#fileId; }
  get path() { return this.#path; }
  get stamp() { return this.#stamp; }
  get size() { return this.#size; }
  get url() { return this.#url; }
  get original() { return this.#original; }
  get bytes() { return this.#bytes; }
  get edited() { return this.#edited; }
  get dirty() { return this.#dirty; }
  get savedSince() { return this.#savedSince; }
  get version() { return this.#version; }
  get clean() { return this.#clean; }
  get afterEdit() { return this.#afterEdit; }
  get undoDepth() { return this.#undo.length; }
  get redoDepth() { return this.#redo.length; }
  get redacted() { return this.#redacted; }
  get redactedSaved() { return this.#redactedSaved; }
  get unsaved() { return this.#unsaved; }
  get madeBy() { return this.#madeBy; }
  get tool() { return this.#tool; }
  get goto() { return this.#goto; }
  get head() { return this.#head; }
  get headCopy() { return this.#headCopy; }
  get reading() { return this.#reading; }
  get source() { return this.#source; }
  get shown() { return this.#shown; }
  get broken() { return this.#broken; }
  get brokenSaid() { return this.#brokenSaid; }
  get forgotten() { return this.#forgotten; }
  get gone() { return this.#gone; }
  get closed() { return this.#closed; }
  /** Unsaved: changes not saved to a file, or a tool's result not saved anywhere (the dot). */
  get needsSaving() { return this.#dirty || this.#unsaved; }

  #emit(what) { if (!this.#closed) this.#listener?.(this, what); }

  // ---- transitions: showing it --------------------------------------------------------
  /** loaded: ``doc`` (pdf.js's) shows it now. Unchanged, it is the state
   *  Undo and Redo can come back to (see annotationsAre). */
  loaded(doc) {
    this.#shown = true;
    this.#clean = this.#dirty ? null : { doc, print: "" };
    this.#emit("loaded");
  }

  /** stashed: another tab takes its place in the viewer. ``tool``: the
   *  annotation tool that was on (null: the annotate bar was closed), to come
   *  back with it; ``bytes``: its unsaved annotations and form entries,
   *  written into its bytes (null when it has none). */
  stashed({ tool = null, bytes = null } = {}) {
    this.#tool = tool;
    if (bytes) { this.#bytes = bytes; this.#edited = true; }
    this.#emit("stashed");
  }

  /** restored: its pages are back on screen; the tool it had is given back once. */
  restored() {
    const tool = this.#tool;
    this.#tool = null;
    return tool;
  }

  /** Where to go once its pages are shown (null: nowhere). */
  aim(goto) { this.#goto = goto || null; }
  /** ... and once there: given back once. */
  arrived() {
    const goto = this.#goto;
    this.#goto = null;
    return goto;
  }

  /** The password that opens it. */
  unlocked(password) { this.#password = password; }

  // ---- transitions: reading its file ------------------------------------------------
  /** A file read in pieces: its first piece is here again (for pdf.js). */
  firstPiece(head) { this.#head = head; }
  /** ... and pdf.js takes it, with ``source`` serving the other pieces. */
  piecesFrom(source) {
    const head = this.#head;
    this.#head = null;
    this.#source = source;
    return head;
  }
  /** Reading the rest of it (a promise of all of it), or no longer (null). */
  readingAll(promise) { this.#reading = promise; }
  /** All of its file is here: the tab holds it as it holds a small file. */
  readInFull(bytes) {
    if (!this.#bytes) this.#bytes = this.#original = bytes;
    this.#headCopy = null;
    this.#emit("read");
  }
  /** Its file was changed or deleted by another program: it can't be read any more. */
  fileGone(why) { if (!this.#gone) this.#gone = why; }
  /** It can't be shown (damaged, or not a PDF). */
  broke() { this.#broken = true; this.#emit("broke"); }
  /** What the broken-file screen says about it now. */
  say(text) { this.#brokenSaid = text || ""; }
  /** Taken off Recent. */
  forgot() { this.#forgotten = true; }

  // ---- transitions: changes -------------------------------------------------------------
  /** annotationChanged: an annotation or form entry was added or changed (pdf.js's storage). */
  annotationChanged() {
    const was = this.#dirty;
    this.#redo = [];  // (Redo of a page change would take this away)
    this.#version++;
    this.#savedSince = false;  // changed again after a save
    this.#dirty = true;
    this.#emit(was ? "annotation" : "dirty");  // ("annotation": one more change to a tab already unsaved)
  }

  /** After pdf.js's Undo or Redo: ``print`` is what the annotations of
   *  ``doc`` are now (see Reader's annotPrint). The same as when it was
   *  opened or saved, in the same document (no page or text change since):
   *  undoneToSaved, it isn't changed any more. Anything else is a change.
   *  Returns "saved" or "changed" when that flipped, else null. */
  annotationsAre(doc, print) {
    const clean = this.#clean;
    if (!clean || clean.doc !== doc) return null;
    const same = print === clean.print;
    if (same === !this.#dirty) return null;
    if (same) return this.undoneToSaved();
    this.#dirty = true;
    this.#savedSince = false;
    this.#version++;
    this.#emit("dirty");
    return "changed";
  }

  /** undoneToSaved: Undo brought it back to how it was opened or last saved. */
  undoneToSaved() {
    this.#dirty = false;
    this.#savedSince = true;
    this.#emit("undone");
    return "saved";
  }

  /** pageEdited: the server rewrote it (pages arranged, a picture or link
   *  changed, signed with an ID, ...). ``before``: the bytes before, for Undo. */
  pageEdited(before, after, { redacted = false } = {}) {
    this.#redo = [];
    this.#undo.push({ bytes: before, redacted: this.#redacted, saved: !this.#dirty });
    if (this.#undo.length > UNDO_KEPT) this.#undo.shift();
    this.#bytes = this.#afterEdit = after;
    this.#edited = true;
    this.#dirty = true;
    this.#savedSince = false;
    this.#clean = null;
    if (redacted) this.#redacted = true;
    this.#version++;
    this.#emit("edited");
  }
  /** textEdited: words changed (the same as a page change, for Undo and saving). */
  textEdited(before, after) { this.pageEdited(before, after); }
  /** redactionsApplied: parts blacked out (Save makes a redacted copy from now on). */
  redactionsApplied(before, after) { this.pageEdited(before, after, { redacted: true }); }

  /** editUndone: the last page or text change taken back; ``goto``: where to
   *  be when it is shown again. Returns the bytes it is back to (null: none). */
  editUndone(goto = null) {
    const last = this.#undo.pop();
    if (!last) return null;
    this.#redo.push({ bytes: this.#bytes, redacted: this.#redacted, saved: !this.#dirty });
    this.#bytes = this.#afterEdit = last.bytes;
    this.#edited = last.bytes !== this.#original;
    // Back to how it was when it was unchanged (as opened or last saved: the
    // same document, though written out again when it had annotations).
    this.#dirty = !last.saved;
    this.#savedSince = last.saved;
    this.#clean = null;
    this.#redacted = last.redacted;  // Undo of Black out: it's not a redacted document any more
    if (!this.#redacted) this.#redactedSaved = false;
    this.#goto = goto;
    this.#version++;
    this.#emit("undone");
    return last.bytes;
  }

  /** editRedone: the last page or text change Undo took back is made again
   *  (``goto`` as for editUndone). Returns the bytes it is back to (null: none). */
  editRedone(goto = null) {
    const next = this.#redo.pop();
    if (!next) return null;
    this.#undo.push({ bytes: this.#bytes, redacted: this.#redacted, saved: !this.#dirty });
    this.#bytes = this.#afterEdit = next.bytes;
    this.#edited = next.bytes !== this.#original;
    this.#dirty = !next.saved;
    this.#savedSince = next.saved;
    this.#clean = null;
    this.#redacted = next.redacted;
    if (!this.#redacted) this.#redactedSaved = false;
    this.#goto = goto;
    this.#version++;
    this.#emit("redone");
    return next.bytes;
  }

  /** made: a tool's result, only in the app until saved (``madeBy``: what made it). */
  made(madeBy) {
    this.#madeBy = madeBy;
    this.#unsaved = true;
    this.#emit("made");
  }

  // ---- transitions: saving --------------------------------------------------------------
  /** saved: what it held at ``version`` is in a file now. ``how``:
   *    "in place"  over its own file: ``bytes`` and the file's new ``stamp``
   *                (``keep``: the tab's bytes become these too);
   *    "as"        a copy the person picked (``made``: a tool's result, now saved somewhere);
   *    "redacted"  a redacted copy beside the original.
   *  ``doc``/``print``: the document shown and its annotations now, the
   *  state Undo can come back to (none when it isn't shown). Returns whether
   *  it is unchanged now (false: it changed after ``version``). */
  saved(how, { version = this.#version, bytes = null, stamp, keep = false, made = false, doc = null, print = "" } = {}) {
    if (this.#closed) return false;
    if (how === "in place") {
      this.#savedOver = { original: this.#original, size: this.#size };
      this.#stamp = stamp;
      this.#original = bytes;
      this.#size = bytes.length;
      if (!this.#bytes || keep) this.#bytes = bytes;
    } else if (how === "redacted") this.#redactedSaved = true;
    if (made) this.#unsaved = false;
    const current = this.#version === version;
    for (const step of [...this.#undo, ...this.#redo]) step.saved = false;  // (what Undo or Redo goes back to isn't what was saved now)
    if (current) {
      this.#dirty = false;
      this.#savedSince = true;
      this.#clean = doc ? { doc, print } : null;
    }
    this.#emit("saved");
    return current;
  }

  /** saveUndone: a save over its file was taken back (the file has what it
   *  had, ``stamp``); the tab keeps the changes, unsaved again. */
  saveUndone(stamp) {
    this.#stamp = stamp;
    // The file holds what it held before (so Undo of a page change back to
    // the bytes that were saved doesn't count as unchanged).
    if (this.#savedOver) { this.#original = this.#savedOver.original; this.#size = this.#savedOver.size; this.#savedOver = null; }
    this.#dirty = true;
    this.#savedSince = false;
    this.#clean = null;
    for (const step of [...this.#undo, ...this.#redo]) step.saved = false;
    this.#emit("unsaved");
  }

  /** closed: its tab was closed; it tells nobody anything any more. */
  closed() {
    if (this.#closed) return;
    this.#emit("closed");
    this.#closed = true;
    this.#listener = null;
    this.#reading = null;
  }
}

// ---- what the screen shows --------------------------------------------------------------

/** Does closing ``tab`` ask first? null if not, else the question's text and
 *  its Save button's label (Save… when it asks where). */
export function closePrompt(tab) {
  if (!tab || !tab.needsSaving) return null;
  return {
    title: `Close “${tab.name}”?`,
    text: tab.dirty ? "Your changes to it haven't been saved." : "It hasn't been saved anywhere yet.",
    save: tab.unsaved || !tab.fileId ? "Save…" : "Save",
  };
}

/** What Save (Ctrl+S) does for ``tab``: "redacted copy" (a tab with
 *  blacked-out parts, never over its original unasked), "already redacted"
 *  (such a copy saved, nothing changed since), "as" (no file of its own:
 *  Save as), "nothing" (no changes) or "in place" (over its file).
 *  ``changed``: annotations or entries not in its bytes yet; ``replace``:
 *  the person chose to replace the original with the redacted version. */
export function saveAction(tab, { changed = false, replace = false } = {}) {
  if (!tab) return "nothing";
  if (tab.redacted && !replace) {
    return tab.redactedSaved && !tab.dirty && (tab.savedSince || !changed) ? "already redacted" : "redacted copy";
  }
  if (!tab.fileId) return "as";
  // (pdf.js keeps what was saved in its storage: unchanged since the save is nothing to save.)
  if (!tab.dirty && (tab.savedSince || !changed)) return "nothing";
  return "in place";
}

/** Everything the reader shows about saving, from the shown tab's state.
 *  ``view``: { shown: a document is in the viewer, annotating: the annotate
 *  bar is open, annotUndo: pdf.js has something to undo, changed:
 *  annotations or entries not in the bytes yet, marked: areas marked for
 *  Redact (Black out is the main action then), active: the reader is the
 *  app's page shown }. */
export function deriveUi(tab, tabs, view = {}) {
  const { shown = false, annotating = false, annotUndo = false, changed = false, marked = false, active = false } = view;
  const on = shown && tab ? tab : null;
  const open = !!(on && !annotating);  // the toolbar's own Save and Undo (the annotate bar has its own)
  // The annotate bar's Save is the main action only once there is something to save.
  const pending = !!(tab && (tab.dirty || (!tab.savedSince && (annotUndo || changed))));
  return {
    dots: new Map(tabs.map((t) => [t.id, t.needsSaving])),
    titleDot: !!(on && on.needsSaving),
    saveNow: open && !!tab.dirty,
    undoNow: open && !!(annotUndo || tab.undoDepth),
    annotSave: {
      main: pending && !marked,
      title: !pending ? "Nothing to save yet (Ctrl+S)"
        : tab.redacted ? "Save a redacted copy beside the original (Ctrl+S)" : "Save your changes (Ctrl+S)",
    },
    // The window's title (null: the reader isn't shown, the app's own stays).
    title: !active ? null : tab ? tab.name + " — ZPDF" : "PDF reader — ZPDF",
    heading: tab ? tab.name : "",
    close: closePrompt(tab),
    ctrlS: saveAction(tab, { changed }),
    anyUnsaved: tabs.some((t) => t.needsSaving),  // closing ZPDF or updating it asks first
  };
}
