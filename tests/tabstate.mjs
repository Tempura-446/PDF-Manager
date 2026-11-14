// TabState's transitions and deriveUi (static/tabstate.js), without a browser:
// it needs nothing of the page. Run by test_tabstate.py:
//   node tabstate.mjs <path to tabstate.js>
// Prints one JSON object: { ok: [...names], failed: [[name, message]] }.
import { pathToFileURL } from "node:url";

const { TabState, deriveUi, closePrompt, saveAction } = await import(pathToFileURL(process.argv[2]).href);
const ok = [], failed = [];
const eq = (a, b, what) => {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${what}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
};
const test = (name, fn) => { try { fn(); ok.push(name); } catch (e) { failed.push([name, String(e.stack || e)]); } };

const bytes = (n) => new Uint8Array([37, 80, 68, 70, n]);
const fresh = (file = { fileId: "f1", path: "/x/A.pdf", stamp: "s1", size: 5 }, heard = []) =>
  new TabState({ bytes: bytes(0), name: "A.pdf", file }, (tab, what) => heard.push(what));
// What the screen shows for ``tab`` alone, shown, with the annotate bar closed.
const ui = (tab, view = {}) => {
  const u = deriveUi(tab, [tab], { shown: true, active: true, ...view });
  return { dot: u.dots.get(tab.id), titleDot: u.titleDot, saveNow: u.saveNow, undoNow: u.undoNow, main: u.annotSave.main,
    close: u.close && u.close.text, ctrlS: u.ctrlS };
};
const DOC = { name: "doc" }, DOC2 = { name: "doc2" };

test("opened: nothing to save", () => {
  const t = fresh();
  eq([t.dirty, t.version, t.edited, t.unsaved, t.needsSaving], [false, 0, false, false, false], "state");
  eq(ui(t), { dot: false, titleDot: false, saveNow: false, undoNow: false, main: false, close: null, ctrlS: "nothing" }, "ui");
  eq(deriveUi(t, [t], { shown: true, active: true }).title, "A.pdf — ZPDF", "title");
  eq(deriveUi(t, [t], { shown: true, active: false }).title, null, "title while another page of the app is shown");
});

test("fields can't be assigned from outside", () => {
  const t = fresh();
  let threw = false;
  try { t.dirty = true; } catch (e) { threw = true; }
  eq([threw, t.dirty], [true, false], "assignment");
});

test("annotate, undo back to saved, redo", () => {
  const heard = [];
  const t = fresh(undefined, heard);
  t.loaded(DOC);
  t.annotationChanged();
  eq([t.dirty, t.version, t.savedSince], [true, 1, false], "after a change");
  eq(ui(t, { annotUndo: true, changed: true }), { dot: true, titleDot: true, saveNow: true, undoNow: true, main: true,
    close: "Your changes to it haven't been saved.", ctrlS: "in place" }, "ui after a change");
  t.annotationChanged();
  eq(heard, ["loaded", "dirty", "annotation"], "what was heard (dirty once, then more changes)");
  // Undo: the annotations are as when it was loaded.
  eq(t.annotationsAre(DOC, ""), "saved", "undo to clean");
  eq([t.dirty, t.savedSince], [false, true], "after undo");
  eq(ui(t, { annotUndo: false, changed: true }), { dot: false, titleDot: false, saveNow: false, undoNow: false, main: false, close: null, ctrlS: "nothing" }, "ui after undo");
  // Redo: different again.
  const v = t.version;
  eq(t.annotationsAre(DOC, "k:1"), "changed", "redo");
  eq([t.dirty, t.version], [true, v + 1], "after redo");
  eq(t.annotationsAre(DOC, "k:1"), null, "no flip, nothing said");
  // A different document (reloaded since): never compared.
  eq(t.annotationsAre(DOC2, ""), null, "another document");
});

test("save in place, then change and undo the save", () => {
  const t = fresh();
  t.loaded(DOC);
  t.annotationChanged();
  const v = t.version, written = bytes(1);
  eq(t.saved("in place", { version: v, bytes: written, stamp: "s2", doc: DOC, print: "k:1" }), true, "saved");
  eq([t.dirty, t.savedSince, t.stamp, t.original === written, t.size], [false, true, "s2", true, 5], "after save");
  eq(ui(t, { annotUndo: true, changed: true }).main, false, "Save quiet although pdf.js can still undo");
  eq(ui(t, { annotUndo: true, changed: true }).ctrlS, "nothing", "Ctrl+S: nothing");
  // Undo in pdf.js back to before the save: a change.
  eq(t.annotationsAre(DOC, ""), "changed", "undo past the save");
  eq(t.annotationsAre(DOC, "k:1"), "saved", "redo to the save");
  // Undo of the save: the file has its old bytes again.
  t.saveUndone("s1");
  eq([t.dirty, t.stamp, t.original === written], [true, "s1", false], "after undoing the save");
});

test("a save that missed a later change", () => {
  const t = fresh();
  t.annotationChanged();
  const v = t.version;
  t.annotationChanged();
  eq(t.saved("as", { version: v }), false, "not current");
  eq(t.dirty, true, "still unsaved");
});

test("page edit and undo", () => {
  const t = fresh();
  t.loaded(DOC);
  const b0 = t.bytes, b1 = bytes(2);
  t.pageEdited(b0, b1);
  eq([t.dirty, t.edited, t.undoDepth, t.bytes === b1, t.clean], [true, true, 1, true, null], "after the edit");
  eq(ui(t), { dot: true, titleDot: true, saveNow: true, undoNow: true, main: true, close: "Your changes to it haven't been saved.", ctrlS: "in place" }, "ui");
  eq(ui(t, { annotating: true }).saveNow, false, "the annotate bar has its own Save");
  eq(t.editUndone({ page: 1 }) === b0, true, "back");
  eq([t.dirty, t.edited, t.undoDepth, t.arrived()], [false, false, 0, { page: 1 }], "after undo");
  eq(t.arrived(), null, "goto given once");
});

test("page edit, undo, redo (a note added, taken back, put back)", () => {
  const t = fresh();
  t.loaded(DOC);
  const b0 = t.bytes, b1 = bytes(2);
  t.pageEdited(b0, b1);
  eq([t.undoDepth, t.redoDepth], [1, 0], "after the edit");
  t.editUndone();
  eq([t.dirty, t.undoDepth, t.redoDepth], [false, 0, 1], "after undo: no dot, one to redo");
  eq(t.editRedone({ page: 1 }) === b1, true, "redo gives the changed bytes back");
  eq([t.dirty, t.edited, t.bytes === b1, t.afterEdit === b1, t.undoDepth, t.redoDepth, t.arrived()],
     [true, true, true, true, 1, 0, { page: 1 }], "after redo: the dot, undo again");
  eq(t.editRedone(), null, "nothing more to redo");
  // Undo again, then a new change: nothing to redo any more.
  t.editUndone();
  t.annotationChanged();
  eq(t.redoDepth, 0, "an annotation forgets the redo");
  t.pageEdited(t.bytes, bytes(5));
  t.editUndone();
  t.pageEdited(t.bytes, bytes(6));
  eq(t.redoDepth, 0, "a page change forgets the redo");
});

test("redo back to the saved state, and after a save", () => {
  const t = fresh();
  t.loaded(DOC);
  const b1 = bytes(2);
  t.pageEdited(t.bytes, b1);
  t.saved("in place", { version: t.version, bytes: b1, stamp: "s2" });
  t.editUndone();
  eq(t.dirty, true, "undo past the save: unsaved");
  t.editRedone();
  eq([t.dirty, t.savedSince], [false, true], "redo back to what was saved: no dot");
  t.editUndone();
  t.saved("as", { version: t.version });
  t.editRedone();
  eq(t.dirty, true, "redo after another save: a change");
});

test("undo of a page edit made after saving annotations: unchanged", () => {
  const t = fresh();
  t.loaded(DOC);
  t.annotationChanged();
  t.saved("in place", { version: t.version, bytes: bytes(3), stamp: "s2", doc: DOC, print: "k:1" });
  const written = bytes(3);  // (written out again for the change: other bytes, the same document)
  t.pageEdited(written, bytes(4));
  t.editUndone();
  eq(t.dirty, false, "no dot after undo");
});

test("undo of a page edit made before a save: changed", () => {
  const t = fresh();
  t.pageEdited(t.bytes, bytes(5));
  t.saved("in place", { version: t.version, bytes: bytes(5), stamp: "s2" });
  t.editUndone();
  eq(t.dirty, true, "the file has the edit; undoing it is a change");
});

test("switch away and back", () => {
  const t = fresh();
  t.loaded(DOC);
  t.annotationChanged();
  const kept = bytes(6);
  t.stashed({ tool: "INK", bytes: kept });
  eq([t.tool, t.bytes === kept, t.edited, t.dirty], ["INK", true, true, true], "stashed");
  t.loaded(DOC2);
  eq(t.clean, null, "a dirty tab has no clean state to come back to");
  eq([t.restored(), t.restored()], ["INK", null], "tool given back once");
  t.stashed({ tool: null });
  eq(t.tool, null, "no tool");
});

test("save as, and a tool's result", () => {
  const t = fresh(null);
  eq(saveAction(t), "as", "no file: Save as");
  t.made({ op: "compress", label: "Compress" });
  eq([t.unsaved, t.needsSaving, t.madeBy.op], [true, true, "compress"], "made");
  eq(closePrompt(t), { title: "Close “A.pdf”?", text: "It hasn't been saved anywhere yet.", save: "Save…" }, "close prompt");
  eq(ui(t).dot, true, "dot");
  t.saved("as", { version: t.version, made: true });
  eq([t.unsaved, t.needsSaving, closePrompt(t)], [false, false, null], "saved somewhere");
});

test("redact, save a copy, Ctrl+S again, undo the black-out", () => {
  const t = fresh();
  t.loaded(DOC);
  t.redactionsApplied(t.bytes, bytes(7));
  eq([t.redacted, t.dirty], [true, true], "redacted");
  eq(ui(t).ctrlS, "redacted copy", "Ctrl+S makes a redacted copy");
  eq(deriveUi(t, [t], { shown: true }).annotSave.title, "Save a redacted copy beside the original (Ctrl+S)", "Save's title");
  eq(saveAction(t, { replace: true }), "in place", "replacing the original is asked for");
  t.saved("redacted", { version: t.version, doc: DOC2, print: "" });
  eq([t.redactedSaved, t.dirty], [true, false], "copy saved");
  eq(ui(t).ctrlS, "already redacted", "not another copy");
  eq(ui(t, { changed: true }).ctrlS, "already redacted", "pdf.js still holding what was saved");
  t.annotationChanged();
  eq(ui(t, { changed: true }).ctrlS, "redacted copy", "changed since: another copy");
  // Undo of the black-out: not a redacted document any more.
  t.saved("redacted", { version: t.version });
  t.editUndone();
  eq([t.redacted, t.redactedSaved], [false, false], "after undo");
  eq(ui(t).ctrlS === "redacted copy" || ui(t).ctrlS === "already redacted", false, "Ctrl+S no longer makes a redacted copy");
});

test("marked for Redact: Save waits, quiet", () => {
  const t = fresh();
  t.annotationChanged();
  eq(ui(t, { annotating: true, marked: true }).main, false, "quiet while marked");
  eq(ui(t, { annotating: true }).main, true, "main otherwise");
});

test("read in pieces", () => {
  const head = bytes(8);
  const t = new TabState({ bytes: null, name: "Big.pdf", file: { fileId: "f2", size: 99, url: "/api/file/f2", head, headCopy: head.slice() } });
  eq([t.bytes, t.original, t.head === head], [null, null, true], "pieces");
  const source = { name: "source" };
  eq(t.piecesFrom(source) === head, true, "pdf.js takes the first piece");
  eq([t.head, t.source === source], [null, true], "after");
  const all = bytes(9);
  t.readInFull(all);
  eq([t.bytes === all, t.original === all, t.headCopy], [true, true, null], "all here");
  t.fileGone("changed");
  t.fileGone("deleted");
  eq(t.gone, "changed", "the first reason stays");
});

test("closed: heard no more", () => {
  const heard = [];
  const t = fresh(undefined, heard);
  t.closed();
  t.annotationChanged();
  eq(heard, ["closed"], "heard");
  eq(t.saved("as", {}), false, "a save finishing after closing changes nothing");
});

test("many tabs: dots and the close of the window", () => {
  const a = fresh(), b = fresh();
  b.annotationChanged();
  const u = deriveUi(a, [a, b], { shown: true });
  eq([u.dots.get(a.id), u.dots.get(b.id), u.titleDot, u.anyUnsaved], [false, true, false, true], "dots");
  const c = fresh(null);
  c.made({ op: "scan" });
  eq(deriveUi(null, [a, c]).anyUnsaved, true, "an unsaved result counts");
  eq(deriveUi(null, [a, b, c]).unsaved, [b.name, c.name], "named for closing the window");
});

console.log(JSON.stringify({ ok, failed }));
