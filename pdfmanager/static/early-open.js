/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Started with a file to show (#open=<id>): pdf.js, its worker, the reader and
// the file itself start loading now, side by side with this page, instead of
// one after the other once the page has set itself up. (Before the
// stylesheets on purpose: a script after them would wait for them.)
(() => {
  const q = new URLSearchParams(location.hash.slice(1)), token = q.get("token");
  const id = (q.get("open") || "").split(",").filter(Boolean)[0];
  if (!token || !id) return;
  for (const href of ["/static/pdfjs/build/pdf.min.mjs", "/static/pdfjs/web/pdf_viewer.mjs", "/static/reader.js"]) {
    const link = document.createElement("link");
    link.rel = "modulepreload"; link.href = href;
    document.head.append(link);
  }
  import("/static/pdfjs.js").then((m) => m.loadPdfjs()).catch(() => {});
  import("/static/reader.js").catch(() => {});
  // The file's first piece (all of a small file): the reader's openById takes
  // it from here. The piece is as long as the reader's own FIRST_BYTES.
  const first = fetch("/api/file/" + encodeURIComponent(id), { cache: "no-store", headers: { "X-Pdfm-Token": token, Range: "bytes=0-4194303" } });
  first.catch(() => {});
  window.zpdfEarly = { id, first };
})();
