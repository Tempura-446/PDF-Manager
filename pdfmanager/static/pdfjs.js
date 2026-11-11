// Loads pdf.js (bundled in /static/pdfjs, see packaging/update_pdfjs.py) once
// for the whole app. The reader, the page previews and the page editors all
// share one background worker: pdf.js would otherwise start a new worker, and
// parse its 1.3 MB of code again, for every document it opens.
//
//   const task = await openDocument({ data, ...DOC_OPTIONS });   // a loading task
//   await destroyTask(task);                                      // when done with it
//
// The shared worker refuses to open a document while another is still being
// freed, so freeing is tracked here and opening waits for it.

export const BASE = new URL("/static/pdfjs/", location.href).href;

export const DOC_OPTIONS = {
  cMapUrl: BASE + "cmaps/", cMapPacked: true,
  standardFontDataUrl: BASE + "standard_fonts/", wasmUrl: BASE + "wasm/", iccUrl: BASE + "iccs/",
  isEvalSupported: false,
};

let loading = null;
let library = null;  // pdf.js once loaded

export function loadPdfjs() {
  loading ||= (async () => {
    // The worker starts (and reads its 1.3 MB of code) while the library loads.
    const src = BASE + "build/pdf.worker.min.mjs";
    let worker = null;
    try { worker = new Worker(src, { type: "module" }); } catch (e) { /* pdf.js starts its own workers */ }
    try {
      const pdfjs = await import(BASE + "build/pdf.min.mjs");
      if (worker) pdfjs.GlobalWorkerOptions.workerPort = worker;
      else pdfjs.GlobalWorkerOptions.workerSrc = src;
      library = pdfjs;
      return pdfjs;
    } catch (e) {
      worker?.terminate();
      throw e;
    }
  })();
  loading.catch(() => { loading = null; });  // try again next time
  return loading;
}

const freeing = new Set();

/** Free a document's loading task. A document that won't let go within
 *  ``wait`` ms (pdf.js waiting for a piece of a file that will never come)
 *  is left behind with the worker it holds up: the documents opened after
 *  it get a new worker, so it can't hold them up too. */
export function destroyTask(task, wait = 10000) {
  if (!task) return Promise.resolve();
  let timer;
  const stuck = new Promise((resolve) => { timer = setTimeout(() => resolve("stuck"), wait); });
  const done = Promise.race([Promise.resolve().then(() => task.destroy()), stuck])
    .then((how) => { if (how === "stuck") freshWorker(); }, () => {})
    .finally(() => { clearTimeout(timer); freeing.delete(done); });
  freeing.add(done);
  return done;
}

function freshWorker() {
  const options = library?.GlobalWorkerOptions;
  if (!options?.workerPort) return;  // (pdf.js starts a worker per document then: nothing shared to hold up)
  try { options.workerPort = new Worker(BASE + "build/pdf.worker.min.mjs", { type: "module" }); } catch (e) { /* the old one stays */ }
}

/** pdfjs.getDocument(), once every document being freed is gone. */
export async function openDocument(params) {
  const pdfjs = await loadPdfjs();
  while (freeing.size) await Promise.all([...freeing]);
  return pdfjs.getDocument(params);
}
