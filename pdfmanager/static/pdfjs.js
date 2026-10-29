// Loads pdf.js (bundled in /static/pdfjs, see packaging/update_pdfjs.py) once
// for the whole app. The reader, the page previews and the page editors all
// share one background worker: pdf.js would otherwise start a new worker, and
// parse its 1.3 MB of code again, for every document it opens.
//
//   const pdfjs = await loadPdfjs();
//   pdfjs.getDocument({ data, ...DOC_OPTIONS })

export const BASE = new URL("/static/pdfjs/", location.href).href;

export const DOC_OPTIONS = {
  cMapUrl: BASE + "cmaps/", cMapPacked: true,
  standardFontDataUrl: BASE + "standard_fonts/", wasmUrl: BASE + "wasm/", iccUrl: BASE + "iccs/",
  isEvalSupported: false,
};

let loading = null;

export function loadPdfjs() {
  loading ||= (async () => {
    const pdfjs = await import(BASE + "build/pdf.min.mjs");
    const src = BASE + "build/pdf.worker.min.mjs";
    try {
      pdfjs.GlobalWorkerOptions.workerPort = new Worker(src, { type: "module" });
    } catch (e) {
      pdfjs.GlobalWorkerOptions.workerSrc = src;  // pdf.js starts its own workers
    }
    return pdfjs;
  })();
  loading.catch(() => { loading = null; });  // try again next time
  return loading;
}
