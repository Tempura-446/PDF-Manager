/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// What the reader's modules share: pdf.js, loaded once (``pdfjs`` and
// ``viewer`` are filled in by loadPdfjs), the icons, making elements, the
// reader's settings in localStorage, and small helpers.

import { BASE, loadPdfjs as loadLibrary } from "./pdfjs.js";

/** Take ``node`` away with its leaving animation (the .out class), or at once
 *  when motion is reduced (the --t token is 0 then). */
export function fadeOut(node) {
  if (!node.isConnected) return;
  const t = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--t")) || 0;
  if (!t) { node.remove(); return; }
  node.classList.add("out");
  setTimeout(() => node.remove(), t * 1.5 + 40);
}

/** Run ``done`` after ``ms``, but not while ``node`` (a toast) is hovered or has
 *  focus: the clock stops then and goes on when the pointer and focus leave.
 *  Returns a function that cancels it. */
export function heldTimer(node, ms, done) {
  let left = ms, since = Date.now(), timer = setTimeout(done, ms), hover = false, focus = false;
  const pause = () => { if (timer) { clearTimeout(timer); timer = 0; left -= Date.now() - since; } };
  const resume = () => { if (!timer && !hover && !focus && left !== null) { since = Date.now(); timer = setTimeout(done, Math.max(left, 2000)); } };
  node.addEventListener("pointerenter", () => { hover = true; pause(); });
  node.addEventListener("pointerleave", () => { hover = false; resume(); });
  node.addEventListener("focusin", () => { focus = true; pause(); });
  node.addEventListener("focusout", (e) => { if (!node.contains(e.relatedTarget)) { focus = false; resume(); } });
  return () => { clearTimeout(timer); timer = 0; left = null; };
}

export let pdfjs = null;   // pdf.js display API
export let viewer = null;  // pdf.js viewer components

/** Fetch a script or stylesheet the page is about to need, alongside what's loading now. */
function preload(rel, href, as = null) {
  if (document.querySelector(`link[rel="${rel}"][href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = rel; link.href = href;
  if (as) link.as = as;
  document.head.append(link);
}

/** pdf.js's stylesheet, once (resolves when it applies, or failed to load).
 *  It is fetched at once but takes its place after the page's own styles, as
 *  it always has: where a stylesheet stands decides which rule wins. */
async function loadViewerCss() {
  if (document.querySelector("link[data-pdfjs]")) return;
  const href = BASE + "web/pdf_viewer.css";
  if (document.readyState === "loading") {
    preload("preload", href, "style");
    await new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, { once: true }));
    if (document.querySelector("link[data-pdfjs]")) return;
  }
  const link = document.createElement("link");
  link.rel = "stylesheet"; link.href = href; link.dataset.pdfjs = "";
  document.head.append(link);
  await new Promise((resolve) => { link.onload = resolve; link.onerror = resolve; });
}

let pdfjsReady = null;
/** pdf.js and its viewer components. Started as soon as this file is read:
 *  the library, its worker, the viewer and its stylesheet load side by side,
 *  so the reader is ready by the time it's opened. */
export function loadPdfjs() {
  pdfjsReady ||= (async () => {
    // (The viewer reads the API from globalThis when it runs, so it can only
    // run after the library; fetching it doesn't have to wait.)
    preload("modulepreload", BASE + "web/pdf_viewer.mjs");
    const css = loadViewerCss();
    pdfjs = await loadLibrary();
    globalThis.pdfjsLib = pdfjs;
    viewer = await import(BASE + "web/pdf_viewer.mjs");
    await css;
  })();
  pdfjsReady.catch(() => { pdfjsReady = null; });  // try again next time
  return pdfjsReady;
}
loadPdfjs().catch(() => {});

export const ICON = {
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  open: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  prev: '<path d="m15 18-6-6 6-6"/>', next: '<path d="m9 18 6-6-6-6"/>',
  up: '<path d="m18 15-6-6-6 6"/>', down: '<path d="m6 9 6 6 6-6"/>',
  minus: '<path d="M5 12h14"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  ccw: '<path d="M4 11a8 8 0 1 1 2.3 5.7"/><path d="M4 4v7h7"/>', cw: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>', check: '<path d="M20 6 9 17l-5-5"/>',
  book: '<path d="M2 5h6a4 4 0 0 1 4 4v11a3 3 0 0 0-3-3H2z"/><path d="M22 5h-6a4 4 0 0 0-4 4v11a3 3 0 0 1 3-3h7z"/>',
  spread: '<rect x="2" y="5" width="9" height="14" rx="1"/><rect x="13" y="5" width="9" height="14" rx="1"/>',
  hand: '<path d="M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.9-6-2.4l-3.6-3.6a2 2 0 0 1 2.8-2.8L7 15"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  full: '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  print: '<path d="M6 9V2h12v7"/><rect x="6" y="14" width="12" height="8"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>',
  save: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>', home: '<path d="M4 11 12 4l8 7"/><path d="M6 9.5V20h12V9.5"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  tool: '<path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-3-3 8-8-1.3-1.3a4 4 0 0 0-5-5z"/><path d="m4 20 4-4"/>',
  twisty: '<path d="m9 6 6 6-6 6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  path: '<path d="M3 18 9 9l5 6 7-10"/><circle cx="3" cy="18" r="1.3"/><circle cx="9" cy="9" r="1.3"/><circle cx="14" cy="15" r="1.3"/><circle cx="21" cy="5" r="1.3"/>',
  straight: '<path d="M5 4v15h15"/><path d="M9 15h7V8" stroke-dasharray="2.5 2.5"/>',
  copypage: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h2"/><path d="M14 12v5M11.5 14.5h5"/>',
  command: '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  pointer: '<path d="m4 4 7 17 2.5-7.5L21 11z"/>',
  highlight: '<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>',
  text: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
  draw: '<path d="M3 17c3-3 5-9 8-9s1 8 4 8 4-4 6-6"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  sign: '<path d="M3 17c2-1 3-5 5-5s0 5 2 5 3-6 5-6 1 6 3 6 2-2 3-2"/><path d="M3 21h18"/>',
  edittext: '<path d="M4 7V5h11v2M9.5 5v13M7 18h4"/><path d="m14 20 6.5-6.5-2-2L12 18v2z"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  ruler: '<path d="M3 16.5 16.5 3 21 7.5 7.5 21z"/><path d="m7.5 12 2 2M10.5 9l2 2M13.5 6l2 2"/>',
  area: '<path d="M4 7 11 3l9 5-2.5 11L6 20z"/><circle cx="4" cy="7" r="1.2"/><circle cx="11" cy="3" r="1.2"/><circle cx="20" cy="8" r="1.2"/>',
  screen: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  note: '<path d="M4 5h16v11H10l-4 4v-4H4z"/><path d="M8 9h8M8 12h5"/>',
  imgedit: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="m3 13 4-4 6 6"/><path d="M14 20h7M17.5 16.5v7" transform="translate(-1 -3)"/>',
  fillable: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h4M7 14h4"/><rect x="13" y="7.5" width="5" height="3" rx=".5"/><rect x="13" y="12.5" width="3" height="3" rx=".5"/>',
  badge: '<path d="M12 2l2.4 1.8 3 .2.9 2.9 2.3 1.9-.9 2.9.9 2.9-2.3 1.9-.9 2.9-3 .2L12 22l-2.4-1.8-3-.2-.9-2.9-2.3-1.9.9-2.9-.9-2.9 2.3-1.9.9-2.9 3-.2z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
  redact: '<path d="M4 6h16M4 18h9"/><rect x="4" y="10" width="13" height="4" rx="1" fill="currentColor"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  rect: '<rect x="4" y="6" width="16" height="12" rx="1"/><circle cx="4" cy="6" r="1.4"/><circle cx="20" cy="18" r="1.4"/>',
  cutout: '<rect x="3" y="4" width="18" height="16" rx="1"/><rect x="9" y="9" width="7" height="6" stroke-dasharray="2 2"/>',
  count: '<circle cx="7" cy="8" r="3"/><circle cx="17" cy="8" r="3"/><circle cx="12" cy="17" r="3"/>',
  angle: '<path d="M4 20h16M4 20 15 6"/><path d="M10.5 20a6.5 6.5 0 0 0-2.3-5"/>',
  csv: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h8"/>',
  pdfin: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M12 11v6M9 14l3 3 3-3"/>',
  underline: '<path d="M7 4v6a5 5 0 0 0 10 0V4"/><path d="M5 20h14"/>',
  strike: '<path d="M16 6.5C15 5 13.6 4.5 12 4.5c-2.2 0-4 1.2-4 3.2 0 1.5 1 2.4 2.6 3"/><path d="M4 12h16"/><path d="M8 17.5c1 1.4 2.4 2 4 2 2.3 0 4-1.3 4-3.3 0-.8-.3-1.5-.8-2"/>',
  shapes: '<rect x="3" y="11" width="9" height="9" rx="1"/><circle cx="16" cy="8" r="5"/>',
  stamp: '<path d="M9 3h6l-1 7h4a2 2 0 0 1 2 2v3H4v-3a2 2 0 0 1 2-2h4z"/><path d="M5 19h14"/>',
  comments: '<path d="M4 5h13v9H9l-3 3v-3H4z"/><path d="M17 9h3v8h-2v3l-3-3h-4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.3 5.3L3 18l3 3 6.4-6.4a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.3-.6-.6-2.3z"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>',
  access: '<circle cx="12" cy="4.5" r="1.8"/><path d="M5 8.5l7 1.5 7-1.5M12 10v4.5M12 14.5 8.5 21M12 14.5l3.5 6.5"/>',  // -- accessibility --
};
export const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;
export const MIN_ZOOM = 0.1, MAX_ZOOM = 10;  // what the viewer can draw

// Annotation colours (highlights use the pale versions, as on paper).
export const COLORS = [["Black", "#000000", "#bdbdbd"], ["Blue", "#1f4fd8", "#80ebff"], ["Red", "#d52b1e", "#ff4f5f"],
  ["Green", "#1f8a3b", "#53ffbc"], ["Yellow", "#b8860b", "#ffff98"]];

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

export const isTyping = (e) => /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) || e.target.isContentEditable;
export const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ } },
};
