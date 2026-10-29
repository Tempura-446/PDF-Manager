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
