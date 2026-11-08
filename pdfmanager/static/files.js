// Saving and opening files with the computer's own Save and Open dialogs in
// a browser window (Edge, Chrome): the File System Access API. The desktop
// window (pywebview) has its own dialogs; other browsers fall back to
// downloads and the file picker.

export const canSave = typeof window.showSaveFilePicker === "function";
export const canOpen = typeof window.showOpenFilePicker === "function";

const TYPES = {
  pdf: ["PDF document", "application/pdf"],
  zip: ["ZIP archive", "application/zip"],
  docx: ["Word document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  xlsx: ["Excel workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
  txt: ["Text file", "text/plain"],
};

/** Ask where to save ``blob`` (suggesting ``name``) and write it there.
 *  Must be called from a click. Returns the chosen file name, or null if
 *  the dialog was cancelled. */
export async function saveAs(blob, name) {
  const ext = (name.match(/\.([^.]+)$/) || [])[1]?.toLowerCase();
  const type = TYPES[ext];
  let handle;
  try {
    handle = await window.showSaveFilePicker({
      suggestedName: name, id: "pdfm-save", startIn: "documents",
      types: type ? [{ description: type[0], accept: { [type[1]]: ["." + ext] } }] : undefined,
    });
  } catch (e) {
    if (e.name === "AbortError") return null;  // cancelled
    throw e;
  }
  const writable = await handle.createWritable();
  await writable.write(blob);
  await writable.close();
  return handle.name;
}

/** Let the user pick PDFs. Returns [{file, handle}] (empty if cancelled). */
export async function openPdfs(multiple = false) {
  let handles;
  try {
    handles = await window.showOpenFilePicker({
      id: "pdfm-open", multiple, startIn: "documents",
      types: [{ description: "PDF documents", accept: { "application/pdf": [".pdf"] } }],
    });
  } catch (e) {
    if (e.name === "AbortError") return [];
    throw e;
  }
  return Promise.all(handles.map(async (handle) => ({ file: await handle.getFile(), handle })));
}

// ---- recently opened files (browser windows) ---------------------------------------
// Files opened with the Open dialog or dropped on the reader are remembered by
// their handle, so they can be reopened later; the browser asks once per
// session for permission to read them again.

const DB = "pdfm", STORE = "recent", LIMIT = 30;

function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "key" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx(mode, fn) {
  const conn = await db();
  try {
    return await new Promise((resolve, reject) => {
      const t = conn.transaction(STORE, mode);
      const result = fn(t.objectStore(STORE));
      t.oncomplete = () => resolve(result?.result ?? result);
      t.onerror = () => reject(t.error);
    });
  } finally { conn.close(); }
}

/** All remembered handles, newest first: [{key, handle, name, opened}]. */
export async function recentHandles() {
  try {
    const all = await tx("readonly", (s) => s.getAll());
    return (all || []).sort((a, b) => b.opened - a.opened);
  } catch (e) { return []; }  // storage unavailable
}

export async function rememberHandle(handle) {
  try {
    const items = await recentHandles();
    let same = null;
    for (const item of items) if (await item.handle.isSameEntry(handle).catch(() => false)) { same = item; break; }
    const entry = { key: same ? same.key : `${Date.now()}-${Math.random().toString(36).slice(2)}`, handle, name: handle.name, opened: Date.now() };
    await tx("readwrite", (s) => {
      s.put(entry);
      for (const old of items.filter((i) => i.key !== entry.key).slice(LIMIT - 1)) s.delete(old.key);
    });
  } catch (e) { /* remembering is a convenience */ }
}

export async function forgetHandle(key) {
  try { await tx("readwrite", (s) => s.delete(key)); } catch (e) { /* ignore */ }
}

/** The file behind a remembered handle (asks for permission if needed;
 *  call from a click). Returns null if permission is refused or it's gone. */
export async function fileFromHandle(handle) {
  try {
    if ((await handle.queryPermission({ mode: "read" })) !== "granted"
        && (await handle.requestPermission({ mode: "read" })) !== "granted") return null;
    return await handle.getFile();
  } catch (e) { return null; }
}
