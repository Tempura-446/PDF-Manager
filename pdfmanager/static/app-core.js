/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
"use strict";
const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
const $ = (id) => document.getElementById(id);
let ops = [], current = null, items = [], lastUrl = null;
let formOp = null;  // the tool whose form (and result) is on screen under the reader
const isDesktop = () => !!(window.pywebview && window.pywebview.api);
const isMac = /Mac/.test(navigator.platform);

// ---- icons ----------------------------------------------------------------
const ICONS = {
  improve: '<path d="M11 4.5 12.6 9l4.4 1.5-4.4 1.6L11 16.5l-1.6-4.4L5 10.5 9.4 9z"/><path d="M18 3v4M16 5h4M18.5 15.5v4M16.5 17.5h4"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M7.5 14h9"/>',
  star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
  home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v10h13V10"/><path d="M10 20v-6h4v6"/>',
  reader: '<path d="M2 5h6a4 4 0 0 1 4 4v11a3 3 0 0 0-3-3H2z"/><path d="M22 5h-6a4 4 0 0 0-4 4v11a3 3 0 0 1 3-3h7z"/>',
  merge: '<path d="M8 4v5a4 4 0 0 0 4 4h0a4 4 0 0 1 4 4v3"/><path d="M16 4v5a4 4 0 0 1-4 4"/><path d="m13 17 3 3 3-3"/>',
  split: '<path d="M12 3v7"/><path d="M12 10 6 16v5M12 10l6 6v5"/><path d="m3 18 3-3 3 3M15 18l3-3 3 3"/>',
  interleave: '<rect x="3" y="4" width="8" height="10" rx="1.5"/><rect x="13" y="10" width="8" height="10" rx="1.5"/><path d="M7 17v2a1 1 0 0 0 1 1h3M17 7V5a1 1 0 0 0-1-1h-3"/>',
  extract: '<rect x="4" y="3" width="11" height="14" rx="2"/><path d="M15 13h3a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-5a2 2 0 0 1-2-2v-2"/>',
  delete: '<path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  reorder: '<path d="M7 4v16M3 8l4-4 4 4M17 20V4M13 16l4 4 4-4"/>',
  reverse: '<path d="M4 7h13l-3-3M20 17H7l3 3"/>',
  rotate: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  resize: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
  crop: '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
  "insert-blank": '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h7"/><path d="M13 3l4 4v4"/><path d="M18 14v7M14.5 17.5h7"/>',
  booklet: '<path d="M12 6.5C10 5 7 4.5 3 4.5v13c4 0 7 .5 9 2 2-1.5 5-2 9-2v-13c-4 0-7 .5-9 2z"/><path d="M12 6.5v13"/>',
  autocrop: '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/><path d="M10 10h4v4h-4z" stroke-dasharray="2 2"/>',
  nup: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
  compress: '<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/>',
  color: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/>',
  ocr: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M8 9h8M8 12h8M8 15h5"/>',
  watermark: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
  number: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
  metadata: '<path d="M20 12 12 20l-8.5-8.5V4h7.5z"/><circle cx="8" cy="8" r="1.5"/>',
  encrypt: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  decrypt: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/>',
  "from-images": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  scan: '<path d="M6 11V4h8l4 4v3"/><path d="M14 4v4h4"/><rect x="3" y="11" width="18" height="8" rx="2"/><path d="M7 15h6"/>',
  "to-images": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="m8 17 3-3 2 2 2-2 2 2"/>',
  text: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  clean: '<path d="m5 19 9-9"/><path d="M14 10 19 5"/><path d="M15 3v2M19 7h2M16.5 3.5l1 1M3 21l2-2"/><path d="M9 4H5a2 2 0 0 0-2 2v4M21 14v4a2 2 0 0 1-2 2h-4"/>',
  bookmarks: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  flatten: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h10M7 13h6"/><path d="M15 17l2 2 3-4"/>',
  "flatten-layers": '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/>',
  form: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h4M7 14h4"/><rect x="13" y="7.5" width="5" height="3" rx=".5"/><rect x="13" y="12.5" width="3" height="3" rx=".5"/>',
  sanitize: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  makesafe: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="M12 8v5M12 16.5v.01"/>',
  "sec-profile": '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  "encrypt-cert": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15.5" r="1.6"/>',
  "decrypt-cert": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/><circle cx="12" cy="15.5" r="1.6"/>',
  "upgrade-protection": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/><path d="m9.5 15.5 2.5-2.5 2.5 2.5M12 13v5"/>',
  sign: '<path d="M3 17c2-1 3-5 5-5s0 5 2 5 3-6 5-6 1 6 3 6 2-2 3-2"/><path d="M3 21h18"/>',
  "sign-cert": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4"/><path d="M14 3v5h5v3"/><circle cx="17" cy="15" r="3"/><path d="m15.5 17.6-.8 3.9 2.3-1.3 2.3 1.3-.8-3.9"/>',
  "verify-sig": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="m9 14 2 2 4-4"/>',
  redact: '<path d="M4 6h16M4 18h9"/><rect x="4" y="10" width="13" height="4" rx="1" fill="currentColor"/>',
  pdfa: '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4"/>',
  comments: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
  web: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  attach: '<path d="m21 11-8.5 8.5a5 5 0 0 1-7-7L14 4a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15.5 7"/>',
  labels: '<path d="M4 4h7l9 9-7 7-9-9z"/><circle cx="8.5" cy="8.5" r="1.5"/>',
  repair: '<path d="M14.7 6.3a4 4 0 0 0-5.3 5.3L3 18l3 3 6.4-6.4a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.3-.6-.6-2.3z"/>',
  "pdfa-check": '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9a2 2 0 0 0 2 2h5M19 9v3"/><path d="m14 18 2 2 4-4"/>',
  // -- accessibility --
  tag: '<circle cx="12" cy="4.5" r="1.8"/><path d="M5 8.5l7 1.5 7-1.5M12 10v4.5M12 14.5 8.5 21M12 14.5l3.5 6.5"/>',
  "access-check": '<circle cx="10" cy="4.5" r="1.6"/><path d="M4.5 8l5.5 1.2L15.5 8M10 9.2v4M10 13.2 7 19M10 13.2l1.5 2.8"/><path d="m14 19 2 2 4-4"/>',
  // -- end of accessibility --
  compare: '<rect x="3" y="4" width="7" height="16" rx="1.5"/><rect x="14" y="4" width="7" height="16" rx="1.5"/><path d="M6 9h1M17 9h1M6 13h1M16 13h3"/>',
  "to-word": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="m8 11 1.5 6 2.5-4 2.5 4 1.5-6"/>',
  "to-excel": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14.5h18M9 9v11M15 9v11"/>',
  "quantities": '<path d="M3 20h18"/><path d="M6 20V10M11 20V5M16 20v-7"/><path d="M19 6l2 2-2 2"/>',
  /* -- pptx and html -- */
  "to-pptx": '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4M8 21h8"/><path d="M10 8.5v5M10 8.5h2.5a1.5 1.5 0 0 1 0 3H10"/>',
  "to-html": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="m10 11-2 2.5 2 2.5M14 11l2 2.5-2 2.5"/>',
  "to-md": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M8 16v-5l2 2.5L12 11v5M15 11v5m0 0-1.5-1.5M15 16l1.5-1.5"/>',
  translate: '<path d="M5 8l6 6M4 14l6-6 2-3M2 5h12M7 2h1"/><path d="M22 22l-5-10-5 10M14 18h6"/>',
  /* -- end of pptx and html -- */
  "from-office": '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><path d="M17 14v6M14 17l3 3 3-3"/>',
  "to-dxf": '<path d="M3 20h18M4 16l5-10 5 6 4-8 2 12"/>',
  "from-drawing": '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M7 15h10M7 15V9h5l5 6"/>',
  "sheet-sets": '<rect x="3" y="5" width="14" height="12" rx="1.5"/><path d="M7 17v2a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1h-2"/><path d="M11 13h4"/>',
  "compare-sets": '<rect x="3" y="5" width="9" height="14" rx="1.5"/><rect x="12" y="5" width="9" height="14" rx="1.5"/><path d="M6 12h3M15 10h3M15 14h3"/>',
  recipe: '<path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1.4"/><circle cx="4" cy="12" r="1.4"/><circle cx="4" cy="18" r="1.4"/>',
  organize: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M8 13h8"/>',
  rename: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/>',
  duplicates: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  zip: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M11 6h1M11 9h1M11 12h1M10 15h3v3h-3z"/>',
  left: '<path d="m15 18-6-6 6-6"/>', right: '<path d="m9 18 6-6-6-6"/>', x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>', alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>', folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4"/>', upload: '<path d="M12 16V5M7 10l5-5 5 5M5 19h14"/>',
  play: '<path d="M7 4v16l13-8z"/>',
};
function icon(name, cls = "i") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || ICONS.file;
  return s;
}

// ---- theme ----------------------------------------------------------------
function applyTheme(v) { if (v) document.documentElement.dataset.theme = v; else delete document.documentElement.dataset.theme; }
try { const t = localStorage.getItem("pdfm-theme") || ""; $("theme").value = t; applyTheme(t); } catch (e) {}
$("theme").addEventListener("change", (e) => { applyTheme(e.target.value); try { localStorage.setItem("pdfm-theme", e.target.value); } catch (err) {} });

// ---- helpers --------------------------------------------------------------
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}
function humanSize(n) {
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + " " + u[i];
}
function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }
/** Show an error above the page. ``detail``: the technical words behind a
 *  plain message (folded away under "Details"); ``focus``: move focus to it. */
function showError(text, { detail = "", focus = false, action = null } = {}) {
  text = String(text || "").replace(/^(?!\S+\.[A-Za-z0-9]{2,5}(?:[\s:]|$))./, (c) => c.toUpperCase());  // (the server's words start in lower case)
  const e = $("error");
  const more = [];
  if (action && action.label && typeof action.onClick === "function") {
    more.push(el("button", { type: "button", class: "btn seal sm", onclick: action.onClick }, icon("download"), action.label));
  }
  if (/Try Repair/.test(text) && current && current.id !== "repair" && ops.some((o) => o.id === "repair")) {
    more.push(el("button", { type: "button", class: "link-btn", onclick: () => select("repair") }, "Open Repair"));
  }
  if (detail && detail.toLowerCase() !== text.toLowerCase()) more.push(el("details", {}, el("summary", {}, "Details"), el("code", {}, detail)));
  e.replaceChildren(icon("alert"), el("span", {}, text, more.length ? el("span", { class: "err-more" }, ...more) : null));
  e.hidden = false;
  if (focus) { e.focus({ preventScroll: true }); e.scrollIntoView({ block: "nearest", behavior: smoothScroll() }); }
}
/** The library's words for a file that can't be read, in plain language with a way out. */
function plainError(message) {
  const m = /^(.+?) is not a readable PDF \((.*)\)$/s.exec(String(message || ""));
  if (!m) return message;
  const name = m[1].split(/[\\/]/).pop();
  return current && current.id === "repair"
    ? `${name} can't be opened, even to repair it — it may not be a PDF, or be too damaged. Remove it, or try another copy.`
    : `${name} can't be opened — it may be damaged. Try Repair, or remove it.`;
}
function clearMessages() {
  $("error").hidden = true; $("result").hidden = true; $("result-table").hidden = true;
  $("run-err").hidden = true;
  clearFieldErrors();
}
/** Required-field messages next to their fields (and the marks on the fields). */
function clearFieldErrors() {
  document.querySelectorAll(".field-err").forEach((m) => {
    const input = document.querySelector(`[aria-describedby~="${m.id}"]`);
    if (input) {
      input.removeAttribute("aria-invalid");
      const rest = input.getAttribute("aria-describedby").split(" ").filter((x) => x && x !== m.id).join(" ");
      if (rest) input.setAttribute("aria-describedby", rest); else input.removeAttribute("aria-describedby");
    }
    m.remove();
  });
}
function fieldError(input, text) {
  const id = input.id + "-err";
  const box = input.closest(".field") || input.parentElement;
  box.append(el("div", { class: "hint field-err", id }, text));
  input.setAttribute("aria-invalid", "true");
  input.setAttribute("aria-describedby", [input.getAttribute("aria-describedby"), id].filter(Boolean).join(" "));
  input.addEventListener("input", function gone() { input.removeEventListener("input", gone); if (document.getElementById(id)) clearFieldErrors(); });
}
// Each window of the app tells the server it's there, so closing one window
// doesn't stop the app while another is still open.
const pageId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)).slice(0, 36);
/** "smooth", unless the system asks for less motion. */
function smoothScroll() { return matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"; }
async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { "X-Pdfm-Token": token, "X-Pdfm-Page": pageId, ...(options.headers || {}) } });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.error || res.statusText); }
  return res;
}

// ---- tools -----------------------------------------------------------------
async function load() {
  if (!token) { showError("Open ZPDF with the link printed in the terminal (it contains a security token)."); return; }
  let data;
  try { data = await (await api("/api/operations")).json(); }
  catch (e) { showError("Could not connect. Restart ZPDF and use the new link."); return; }
  ops = [{ id: "home", group: "", label: "Start screen", files: "home", help: "" }, ...data.operations];
  $("version").textContent = "Version " + data.version;
  $("find-label").textContent = `Search ${ops.filter((o) => o.group).length} tools`;
  $("find-keys").textContent = isMac ? "⌘ K" : "Ctrl K";
  renderTools();
  listen();
  setTimeout(checkForUpdates, 4000);
  scheduleWatchNews(1000);
  const openIds = (new URLSearchParams(location.hash.slice(1)).get("open") || "").split(",").filter(Boolean);
  const actionId = new URLSearchParams(location.hash.slice(1)).get("action");
  if (actionId) {  // started from Explorer's right-click menu
    select("home");
    runAction(actionId);
    return;
  }
  if (openIds.length) {
    await openInReaderById(openIds);
    return;
  }
  select("home");
  // Load the reader and the PDF engine in the background once the app is
  // idle, so opening a PDF later doesn't wait for them.
  const warmUp = () => import("/static/reader.js").then((m) => m.loadPdfjs()).catch(() => {});
  if (window.requestIdleCallback) requestIdleCallback(warmUp, { timeout: 2000 }); else setTimeout(warmUp, 1000);
}

// Saving with the computer's own dialogs in a browser window (static/files.js).
let filesLib = null;
const filesReady = import("/static/files.js").then((m) => { filesLib = m; }).catch(() => {});

// ---- toasts and motion ------------------------------------------------------------
/** The motion token (--t) in ms, times ``k``: 0 when the system asks for less motion. */
function motionMs(k = 1) { return (parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--t")) || 0) * k; }
/** Take ``node`` away with its leaving animation (.out), or at once with no motion. */
function fadeAway(node) {
  const ms = motionMs(1.5);
  if (!ms) { node.remove(); return; }
  node.classList.add("out");
  setTimeout(() => node.remove(), ms + 40);
}
/** A question in the app's own dialog, never the browser's confirm(): the
 *  reader's dialog look (reader.css .rd-dialog). ``buttons``: [value, label,
 *  main?], named by what they do. Resolves with the pressed button's value;
 *  Cancel and Escape give "cancel" and do nothing. Focus goes back where it was. */
function appAsk(title, text, buttons) {
  let objMode = false, danger = false;
  if (title && typeof title === "object" && !Array.isArray(title)) {
    objMode = true;
    const opts = title;
    title = opts.title || "";
    text = opts.text || "";
    danger = !!opts.danger;
    buttons = [[ "cancel", opts.no || "Cancel", false ], [ "ok", opts.yes || "OK", true ]];
  }
  buttons = Array.isArray(buttons) && buttons.length ? buttons : [[ "cancel", "Cancel", false ], [ "ok", "OK", true ]];
  return new Promise((resolve) => {
    const before = document.activeElement;
    const form = el("form", { method: "dialog" }, el("h3", {}, title), ...[text].flat().filter(Boolean).map((t) => el("p", {}, t)),
      el("div", { class: "actions" }, ...buttons.map(([value, label, main]) => el("button", main
        ? { value, class: danger ? "primary danger" : "primary", type: "submit" } : { value, type: "button", onclick: () => dialog.close(value) }, label))));
    const dialog = el("dialog", { class: "rd-dialog app-ask", "aria-label": title }, form);
    dialog.addEventListener("cancel", () => { dialog.returnValue = "cancel"; });
    dialog.addEventListener("close", () => {
      const val = dialog.returnValue || "cancel";
      resolve(objMode ? val === "ok" : val);
      dialog.remove();
      if (before && before.isConnected && before !== document.body) before.focus({ preventScroll: true });
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}
window.appAsk = appAsk;
let toastEl = null, toastTimer = 0, toastHold = () => {};
/** A short note that rises in and fades out: the reader's own toast in the
 *  reader, the same look (.toast) on the start screen and the tool pages. */
function appToast(message, ms = 4000, action = null) {
  if (!message) return;
  if (inReader() && reader) { reader.toast(message, ms, action); return; }
  toastEl?.remove();
  const t = toastEl = el("div", { class: "toast", role: "status" }, el("span", {}, message));
  if (action) t.append(el("button", { type: "button", onclick: () => { toastHold(); fadeAway(t); action[1](); } }, action[0]));
  document.body.append(t);
  clearTimeout(toastTimer);
  toastHold();
  toastHold = () => {};
  if (action) toastHold = holdWhileUsed(t, Math.max(ms, 10000), () => fadeAway(t));  // 10 s at least for Undo
  else toastTimer = setTimeout(() => fadeAway(t), ms);
}
/** Run ``done`` after ``ms``, but not while ``node`` is hovered or has focus (the
 *  clock stops, and goes on when they leave). Returns a function that cancels it.
 *  (The reader's heldTimer is the same.) */
function holdWhileUsed(node, ms, done) {
  let left = ms, since = Date.now(), timer = setTimeout(done, ms), hover = false, focus = false;
  const pause = () => { if (timer) { clearTimeout(timer); timer = 0; left -= Date.now() - since; } };
  const resume = () => { if (!timer && !hover && !focus && left !== null) { since = Date.now(); timer = setTimeout(done, Math.max(left, 2000)); } };
  node.addEventListener("pointerenter", () => { hover = true; pause(); });
  node.addEventListener("pointerleave", () => { hover = false; resume(); });
  node.addEventListener("focusin", () => { focus = true; pause(); });
  node.addEventListener("focusout", (e) => { if (!node.contains(e.relatedTarget)) { focus = false; resume(); } });
  return () => { clearTimeout(timer); timer = 0; left = null; };
}

// ---- reader -----------------------------------------------------------------
let reader = null;
async function ensureReader() {
  if (reader) return reader;
  const host = $("reader-host");
  host.replaceChildren(el("div", { class: "reader-loading" }, "Loading the reader…"));
  const [{ Reader }, { renderFormProps }] = await Promise.all([import("/static/reader.js"), import("/static/formprops.js")]);
  host.replaceChildren();
  reader = new Reader(host, {
    token, isDesktop, api: () => (window.pywebview ? window.pywebview.api : null),
    // (-- accessibility --: the reader checks accessibility itself, in a dialog)
    tools: ops.filter((o) => o.files === "pdf" && o.id !== "access-check").map((o) => [o.id, o.label]),
    onEdit: (toolId, file, password) => {
      select(toolId);
      items = [];
      if (password) { $("pw").value = password; $("pw-box").open = true; }
      addFiles([file]);
    },
    onAllTools: () => toggleFlyout("all"),
    onCommands: () => openPalette(),
    onHome: () => select("home"),
    onChange: () => fpSync(),
    // Closing the desktop window asks the server what's unsaved (see gui.py's may_close).
    onUnsaved: (names) => jsonApi("/api/unsaved", { page: pageId, names }).catch(() => {}),
    recent: (host) => fillRecent(host, { onEmpty: () => { const box = host.closest(".rd-recent"); if (box) box.hidden = true; } }),
    onFormHint: (tab) => { if (fp.tab === tab) fpSuggest(); },
    onFormSelect: (selected, pages, tab) => fpFormSelect(selected, pages, tab, renderFormProps),
    onFormCheck: (issues, tab) => fpFormCheck(issues, tab),
    onRepair: (tab) => repairInReader(tab),
    onBrokenWhy: (tab) => brokenFacts(tab),
    onOpenWithId: (tab, id, password) => openWithIdInReader(tab, id, password),
    onForgetRecent: (tab) => jsonApi("/api/recent/forget", { path: tab.path }),
  });
  return reader;
}
const inReader = () => current && current.files === "reader";
async function openInReader(file) {
  await select("reader");
  reader.openFile(file, $("pw").value || null);  // the password typed for the tools, if any
}

// ---- the file panel (beside the reader's pages) -------------------------------------
// What the shown document is (its facts), what to do with it (suggestions,
// six common tools, All tools), and a tool's options right there with Run.
// The result opens as a new reader tab beside the original. Tools that need
// the page or a board, or several files, go to their own page as before.
// ("sign" is the reader's own Sign: draw, type or pick a picture of your
// signature and place it. The certificate tool is "Digital ID", in All tools.)
const COMMON_TOOLS = [["split", "Split"], ["sign", "Sign"], ["to-word", "To Word"], ["pdfa", "Archive (PDF/A)"],
  ["to-excel", "To Excel"], ["encrypt", "Add a password"]];
/** Sign the shown document with a drawn, typed or pictured signature (the
 *  reader's Sign dialog), on ``page`` if given (a printed line to sign on),
 *  on the line itself when the facts say where it is (``line``: page, rect, text). */
async function fpSignWithPen(page = 0, line = null) {
  if (!reader?.doc) return;
  if (page) reader.goToPage(page);
  await reader.sign(line && line.page === page && Array.isArray(line.rect) ? { line } : {});
}
/** Tools that run in the panel: one PDF in, nothing to draw or arrange on the pages. */
const runsInPanel = (op) => !!op && op.files === "pdf" && !op.board && !op.panel && !((op.min || 1) >= 2);
const fp = { tab: null, key: "", seq: 0, timer: 0, items: new Map(), open: null, facts: null, grid: new Map() };
const capital = (text) => String(text || "").replace(/^(?!\S+\.[A-Za-z0-9]{2,5}(?:[\s:]|$))./, (c) => c.toUpperCase());

/** The facts as chips: factsLine's parts, "tagged" too; what needs doing stands out. */
function factChips(facts) {
  const line = factsLine(facts);
  if (!line) return [];
  const parts = line.split(" · ");
  if (facts.tagged && facts.pages != null) {
    const at = parts.findIndex((p) => p === "PDF/A" || p === "protected");
    parts.splice(at < 0 ? parts.length : at, 0, "tagged");
  }
  const chips = parts.map((text) => ({ text, flag: text === "scanned, no text yet", bad: text === "can't be read" }));
  if (facts.weak_protection) {
    const at = chips.findIndex((c) => c.text === "weak protection" || c.text === "protected");
    const how = /40-bit/.test(facts.weak_protection) ? "is broken in minutes on an ordinary computer" : "is old and no longer considered safe";
    const weak = { text: "weak protection", warn: true,
      title: `Its encryption (${facts.weak_protection}) ${how}, whatever the password. Upgrade protection writes it with AES-256, same password.` };
    if (at >= 0) chips[at] = weak; else chips.push(weak);
  }
  // What the file can do that matters (risks.py), said first and in full on hover.
  const risks = facts.risks || [];
  if (risks.length) {
    chips.unshift({ text: facts.risk === "high" ? "could do harm" : facts.risk === "medium" ? "check before trusting" : "points at other files",
      bad: facts.risk === "high", warn: facts.risk === "medium", flag: facts.risk === "low",
      title: risks.map((r) => `${r.title}: ${r.detail}${r.examples?.length ? " (" + r.examples.slice(0, 2).join("; ") + ")" : ""}`).join("\n") });
  }
  return chips;
}

/** The reader changed tabs, or the shown document changed: follow it. */
function fpSync() {
  if (!reader) return;
  const tab = reader.doc ? reader.activeTab : null;
  if (!tab) { clearTimeout(fp.timer); fp.tab = null; fp.key = ""; reader.side.replaceChildren(); return; }
  const fresh = tab !== fp.tab;
  if (fresh) fpRender(tab);
  else if (fp.name.textContent !== tab.name) fp.name.textContent = tab.name;
  // Facts again when it changed (saved, edited); a moment later, so a run of edits asks once.
  const key = [tab.id, tab.version || 0, tab.dirty ? 1 : 0, tab.fileId || "", tab.password ? 1 : 0, tab.madeBy?.op || ""].join("|");  // (what made it: a Compress result isn't offered Compress)
  if (key === fp.key) return;
  fp.key = key;
  clearTimeout(fp.timer);
  // (A document just opened: once its first page is on screen, not while it's being read and drawn.)
  if (fresh) reader.shown().then(() => { if (fp.tab === tab && fp.key === key) fpFacts(tab, key); });
  else fp.timer = setTimeout(() => fpFacts(tab, key), 700);
}

async function fpFacts(tab, key) {
  const seq = ++fp.seq;
  let facts = null;
  try {
    if (reader.isPristine(tab) && !tab.password || (!tab.bytes && tab.fileId)) {
      const madeBy = tab.madeBy?.op ? "?made_by=" + encodeURIComponent(tab.madeBy.op) : "";
      facts = await (await api("/api/facts/" + encodeURIComponent(tab.fileId) + madeBy)).json();
    } else if (tab.bytes) {
      const body = new FormData();
      body.append("file", new Blob([tab.bytes], { type: "application/pdf" }), "file.pdf");
      if (tab.password) body.append("password", tab.password);
      if (tab.madeBy?.op) body.append("made_by", tab.madeBy.op);
      facts = await (await api("/api/facts", { method: "POST", body })).json();
    }
  } catch (e) { facts = null; }  // no facts: the name and the tools still show
  if (seq !== fp.seq || fp.tab !== tab || fp.key !== key) return;  // another document, or changed since
  fpShowFacts(facts);
}

function fpRender(tab) {
  fp.tab = tab;
  fp.items = new Map();
  fp.grid = new Map();  // the common tools' own items, while a suggestion card stands in for one
  fp.facts = null;
  fp.open = null;
  fp.name = el("h2", { class: "fp-name", id: "fp-name" }, tab.name);
  fp.chips = el("div", { class: "fp-facts", role: "list", "aria-label": "About this file", "aria-busy": "true" },
    ...[58, 52, 120].map((w) => el("span", { class: "fp-fact wait", style: `width:${w}px`, "aria-hidden": "true" })),
    el("span", { class: "sr-only", role: "listitem" }, "Reading the file…"));
  fp.note = el("div", { class: "fp-note" });
  fp.chosen = el("section", { class: "fp-sec", hidden: true, "aria-labelledby": "fp-chosen-h" },
    el("h3", { class: "lbl", id: "fp-chosen-h" }, "Chosen tool"), el("div", { class: "fp-list" }));
  fp.sug = el("section", { class: "fp-sec", hidden: true, "aria-labelledby": "fp-sug-h" },
    el("h3", { class: "lbl", id: "fp-sug-h" }, "Suggested for this file"), el("div", { class: "fp-list" }));
  const grid = el("div", { class: "fp-more" });
  for (const [id, label] of COMMON_TOOLS) {
    if (id === "sign") {
      grid.append(el("button", { type: "button", class: "fp-tool", "data-op": "sign", "aria-haspopup": "dialog",
        title: "Sign: draw or type your signature, or use a picture of it, and place it on the page", onclick: () => fpSignWithPen() },
        icon("sign"), el("span", {}, label)));
      continue;
    }
    const op = ops.find((o) => o.id === id);
    if (!op) continue;
    const btn = el("button", { type: "button", class: "fp-tool", "data-op": id, title: op.label + (TAGLINES[id] ? ": " + TAGLINES[id] : "") },
      icon(id), el("span", {}, label));
    grid.append(btn);
    fpItem(op, btn, grid);
  }
  const count = ops.filter((o) => o.group).length;
  const all = el("button", { type: "button", class: "fp-all", "aria-haspopup": "dialog", "aria-controls": "flyout",
    onclick: () => toggleFlyout("all") }, `All ${count} tools`, icon("right"));
  const common = el("section", { class: "fp-sec", "aria-labelledby": "fp-more-h" },
    el("h3", { class: "lbl", id: "fp-more-h" }, "Or do something else"), grid, all);
  fp.common = common;
  fp.formProps = el("section", { class: "fp-sec fp-formprops", hidden: true });
  fp.formCheck = el("section", { class: "fp-sec fp-formcheck", hidden: true });
  reader.side.replaceChildren(el("div", { class: "fp" },
    el("div", { class: "fp-head" }, fp.name, fp.chips), fp.formProps, fp.formCheck, fp.note, fp.chosen, fp.sug, common));
  fpShowNote(tab);
}

function fpFormSelect(selected, pages, tab, renderFormProps) {
  if (!fp.formProps || (tab && fp.tab !== tab)) return;
  const active = !!(selected && selected.length);
  fp.formProps.hidden = !active;
  if (active && fp.formCheck) fp.formCheck.hidden = true;
  const checkOpen = !!(fp.formCheck && !fp.formCheck.hidden);
  const hideRest = active || checkOpen;
  if (fp.common) fp.common.hidden = hideRest;
  if (fp.note) fp.note.hidden = hideRest;
  if (fp.chosen) fp.chosen.hidden = hideRest || !fp.chosen.querySelector(".fp-list")?.children.length;
  if (fp.sug) fp.sug.hidden = hideRest || !fp.sug.querySelector(".fp-list")?.children.length;
  if (active) {
    renderFormProps(fp.formProps, selected, pages, reader);
    if (reader.side.hidden) reader.setPanel?.(true);
  } else {
    fp.formProps.replaceChildren();
  }
}

function fpFormCheck(issues, tab) {
  if (!fp.formCheck || (tab && fp.tab !== tab)) return;
  if (issues == null) {
    fp.formCheck.hidden = true;
    fp.formCheck.replaceChildren();
    const active = !!(fp.formProps && !fp.formProps.hidden);
    if (!active) {
      if (fp.common) fp.common.hidden = false;
      if (fp.note) fp.note.hidden = false;
      if (fp.chosen) fp.chosen.hidden = !fp.chosen.querySelector(".fp-list")?.children.length;
      if (fp.sug) fp.sug.hidden = !fp.sug.querySelector(".fp-list")?.children.length;
    }
    return;
  }
  if (fp.formProps) fp.formProps.hidden = true;
  if (fp.common) fp.common.hidden = true;
  if (fp.note) fp.note.hidden = true;
  if (fp.chosen) fp.chosen.hidden = true;
  if (fp.sug) fp.sug.hidden = true;
  fp.formCheck.hidden = false;
  const closeBtn = el("button", { type: "button", class: "rd-btn compact", title: "Close check list", "aria-label": "Close check list", onclick: () => fpFormCheck(null, tab) }, "×");
  const head = el("div", { class: "fp-prop-title" },
    el("h3", { class: "lbl", style: "margin:0" }, issues.length ? `Form check (${issues.length})` : "Form check"),
    closeBtn);
  const body = !issues.length
    ? el("div", { class: "fp-checkok" }, "No issues found — this form is ready.")
    : el("div", { class: "fp-checklist" }, ...issues.map((issue) =>
        el("button", {
          type: "button", class: "fp-checkitem", "data-kind": issue.kind,
          onclick: () => reader.selectFormField(issue.page, issue.index),
        }, issue.message)));
  fp.formCheck.replaceChildren(head, body);
  if (reader.side.hidden) reader.setPanel?.(true);
}

function fpShowFacts(facts) {
  const chips = factChips(facts);
  fp.chips.removeAttribute("aria-busy");
  fp.chips.hidden = !chips.length;
  fp.chips.replaceChildren(...chips.map((c) => el("span", { class: "fp-fact" + (c.warn ? " warn" : c.flag ? " flag" : c.bad ? " bad" : ""), role: "listitem", ...(c.title ? { title: c.title, tabindex: "0" } : {}) },
    c.bad ? icon("alert") : null, c.text)));
  fp.facts = facts;
  fpSuggest();
}

// Suggestions, most useful first, three at most: the server's (from the facts)
// and the reader's own "Make it fillable" (a printed form it found on the first pages).
const SUGGESTION_ORDER = ["makesafe", "upgrade-protection", "ocr", "compress", "fillable", "verify-sig", "sign", "repair"];
const FILLABLE = { op: "fillable", title: "Make it fillable", why: "It looks like a form you can't type in yet" };
function fpSuggest() {
  if (!fp.tab || !fp.sug) return;
  const list = fp.sug.querySelector(".fp-list"), chosen = fp.chosen.querySelector(".fp-list");
  const known = (s) => s.op === "fillable" || s.op === "sign" || ops.some((o) => o.id === s.op && o.files === "pdf");
  const rank = (s) => { const i = SUGGESTION_ORDER.indexOf(s.op); return i < 0 ? SUGGESTION_ORDER.length : i; };
  const all = [...(fp.facts?.suggestions || [])];
  if (reader.looksLikeFlatForm(fp.tab)) all.push(FILLABLE);
  const wanted = all.filter(known).sort((a, b) => rank(a) - rank(b)).slice(0, 3);
  // The open one stays as it is; one already chosen (from the palette) isn't repeated.
  const cards = wanted.map((s) => {
    if (s.op === "fillable") return list.querySelector('.fp-card[data-op="fillable"]') || fpFillableCard(s);
    if (s.op === "sign") {  // (kept as it is, but signing where the facts now say)
      const card = list.querySelector('.fp-card[data-op="sign"]') || fpSignCard(s);
      card.sug = s;
      return card;
    }
    const had = fp.items.get(s.op);
    if (had && had.home === list) return had.card;
    if (had && had.home === chosen) return null;
    if (had) {  // one of the common tools below: the card stands in for it while it's suggested
      if (fp.open === had) fpCollapse(had, false);
      had.box?.remove(); had.box = null;
      fp.grid.set(s.op, had);
    }
    return fpCard(ops.find((o) => o.id === s.op), s.title, s.why, list);
  }).filter(Boolean);
  for (const old of [...list.children]) {
    if (cards.includes(old)) continue;
    const id = old.dataset.op;
    if (id !== "fillable" && id !== "sign") {
      fpForget(id);
      const own = fp.grid.get(id);
      if (own) { fp.items.set(id, own); fp.grid.delete(id); }
    }
    old.remove();
  }
  list.replaceChildren(...cards);
  fp.sug.hidden = !cards.length;
}

/** "Make it fillable": the same as the reader's bar button (which stays). */
function fpFillableCard(s) {
  const head = el("button", { type: "button", class: "fp-card-head", "data-op": "fillable", title: "Find the form's fields so it can be filled in here",
    onclick: () => reader.makeFillable() },
    el("span", { class: "fp-ico" }, icon("form")), el("span", {}, el("strong", {}, s.title), el("span", {}, s.why)), icon("right", "i fp-go"));
  return el("div", { class: "fp-card", "data-op": "fillable", "data-open": "false" }, head);
}

/** "Sign it": the reader's Sign dialog, then the signature goes on the page. */
function fpSignCard(s) {
  const head = el("button", { type: "button", class: "fp-card-head", "data-op": "sign", "aria-haspopup": "dialog",
    title: "Draw or type your signature, or use a picture of it, and place it on the page", onclick: () => { const now = card.sug || s; fpSignWithPen(now.page || 0, now.line || null); } },
    el("span", { class: "fp-ico" }, icon("sign")), el("span", {}, el("strong", {}, s.title), el("span", {}, s.why)), icon("right", "i fp-go"));
  const card = el("div", { class: "fp-card", "data-op": "sign", "data-open": "false" }, head);
  return card;
}

/** A card: the tool's name and why, opening to its options. */
function fpCard(op, title, why, home) {
  const head = el("button", { type: "button", class: "fp-card-head", "data-op": op.id },
    el("span", { class: "fp-ico" }, icon(op.id)), el("span", {}, el("strong", {}, title), why ? el("span", {}, why) : null),
    icon(runsInPanel(op) ? "right" : "reader", "i fp-go"));
  const card = el("div", { class: "fp-card", "data-op": op.id, "data-open": "false" }, head);
  fpItem(op, head, home, card);
  return card;
}

/** Wire a tool's button: open its options here, or hand the file to its page. */
function fpItem(op, btn, home, card = null) {
  const item = { op, btn, home, card, box: null };
  fp.items.set(op.id, item);
  if (runsInPanel(op)) {
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", `fp-opts-${op.id}`);
    btn.addEventListener("click", () => {
      const now = fp.items.get(op.id) || item;  // a suggestion card may stand in for a common tool
      if (fp.open === now) fpCollapse(now, true); else fpExpand(now);
    });
  } else {
    btn.title = `${op.label}: opens in its own page with this file`;
    btn.addEventListener("click", () => reader.editWith(op.id));
  }
  return item;
}
function fpForget(id) { const item = fp.items.get(id); if (item && fp.open === item) fp.open = null; fp.items.delete(id); }

function fpExpand(item, focus = true) {
  if (fp.open && fp.open !== item) fpCollapse(fp.open, false);
  if (!item.box) item.box = fpOptions(item);
  if (item.card) { if (item.box.parentNode !== item.card) item.card.append(item.box); }
  else if (item.btn.nextElementSibling !== item.box) item.btn.after(item.box);  // the whole row under its button
  const opening = fpSlide(item.box, true);
  fpExpanded(item, true);
  fp.open = item;
  if (focus) (item.box.querySelector("input:not([type=hidden]), select, textarea, button") || item.box).focus({ preventScroll: true });
  // Once open, as much of it as fits comes into view.
  const box = item.box, reveal = () => { if (box.isConnected && !box.hidden) box.scrollIntoView({ block: "nearest", behavior: smoothScroll() }); };
  if (opening) opening.finished.then(reveal, () => {}); else reveal();
}
function fpCollapse(item, focus) {
  if (!item.box) return;
  fpSlide(item.box, false);
  fpExpanded(item, false);
  if (fp.open === item) fp.open = null;
  if (focus) item.btn.focus();
}
/** Every button that opens these options says whether they're open. */
function fpExpanded(item, open) {
  item.btn.setAttribute("aria-expanded", String(open));
  item.card?.setAttribute("data-open", String(open));
  for (const b of reader.side.querySelectorAll(`[aria-controls="fp-opts-${item.op.id}"]`)) b.setAttribute("aria-expanded", String(open));
}
/** Open or close a tool's options smoothly: the height follows, so what is
 *  below moves rather than jumps. At once when the system asks for less motion. */
function fpSlide(box, open) {
  for (const a of box.getAnimations()) a.cancel();
  box.classList.remove("sliding");
  const ms = motionMs(1.8);
  if (!ms || !box.animate || (!open && box.hidden)) { box.hidden = !open; return null; }
  box.hidden = false;
  const cs = getComputedStyle(box);
  const full = { height: box.offsetHeight + "px", paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom, opacity: 1 };
  const none = { height: "0px", paddingTop: "0px", paddingBottom: "0px", opacity: 0 };
  box.classList.add("sliding");
  const run = box.animate(open ? [none, full] : [full, none], { duration: ms, easing: "cubic-bezier(.2, .8, .2, 1)" });
  run.onfinish = () => { box.classList.remove("sliding"); if (!open) box.hidden = true; };
  return run;
}

/** A tool's options (the tool page's fields), Run, and what came of it. */
function fpOptions(item) {
  const { op } = item, prefix = `fp-${op.id}-`;
  const form = el("form", { class: "fields fp-form", autocomplete: "off", novalidate: true });
  renderFields(op, form, prefix, "Nothing to choose here: just run it.");
  // A choice too long for the panel ("Any changes except extracting pages"): smaller type there.
  for (const sel of form.querySelectorAll("select")) {
    if (Math.max(0, ...[...sel.options].map((o) => o.textContent.length)) > 30) sel.closest(".field")?.classList.add("fp-long");
  }
  form.addEventListener("input", () => updateVisibility(op, form, prefix));
  form.addEventListener("submit", (e) => { e.preventDefault(); fpRun(item); });
  const runBtn = el("button", { type: "button", class: "btn seal fp-run" }, el("span", { class: "spinner" }),
    icon("play", "i btn-icon"), op.action || "Run");
  const cancel = el("button", { type: "button", class: "btn secondary fp-cancel", hidden: true }, "Cancel");
  const bar = el("div", { class: "fp-bar", role: "progressbar", "aria-label": `${op.label}: progress`, "aria-valuemin": "0", "aria-valuemax": "100" }, el("i"));
  const status = el("p", { class: "fp-status", "aria-live": "polite" });
  const progress = el("div", { class: "fp-progress", hidden: true }, bar, status);
  const error = el("p", { class: "fp-err", role: "alert", hidden: true });
  const done = el("div", { class: "fp-done", hidden: true, "aria-live": "polite" });
  const extra = op.id === "sign-cert" ? el("p", { class: "fp-hint" }, "No Digital ID? ",
    el("button", { type: "button", class: "link-btn", onclick: () => fpSignWithPen() }, "Sign with a drawn or typed signature"), " instead.") : null;
  Object.assign(item, { form, runBtn, cancel, bar, status, progress, error, done });
  runBtn.addEventListener("click", () => fpRun(item));
  cancel.addEventListener("click", async () => {
    if (!item.job) return;
    cancel.disabled = true; cancel.textContent = "Cancelling…";
    try { await api(`/api/job/${item.job}/cancel`, { method: "POST" }); } catch (e) { /* it may just have finished */ }
  });
  return el("div", { class: "fp-opts" + (item.card ? "" : " wide"), id: `fp-opts-${op.id}`, role: "group", "aria-label": `${op.label}: options`, tabindex: "-1" },
    form, extra, el("div", { class: "fp-actions" }, runBtn, cancel), progress, error, done);
}

/** Run the tool on the shown document: by its id when the file on this
 *  computer is what's shown, else its bytes as they are now (unsaved changes
 *  included; the tab keeps them). */
async function fpRun(item) {
  const { op, error, done, status, runBtn } = item, prefix = `fp-${op.id}-`;
  const tab = fp.tab;
  if (item.running || !tab) return;
  const fail = (text) => {
    if (item.box.isConnected) { error.textContent = text; error.hidden = false; } else reader.toast(`${op.label}: ${text}`, 8000);
  };
  error.hidden = true; done.hidden = true;
  const missing = firstMissing(op, prefix);
  if (missing) { fail(`Please fill in “${missing.label}”.`); $(prefix + missing.key).focus(); return; }
  const { opts, extra } = collectOptions(op, prefix);
  item.running = true;
  runBtn.disabled = true; runBtn.classList.add("busy");
  status.textContent = op.id === "ocr" ? "Recognising text: this can take a while…" : "Working…";
  item.bar.firstChild.style.width = "0";
  item.progress.hidden = false;
  try {
    const body = new FormData();
    body.append("op", op.id);
    if (tab.password) body.append("password", tab.password);
    for (const [k, v] of Object.entries(opts)) body.append(k, v);
    for (const [k, v] of Object.entries(extra)) for (const file of [v].flat()) body.append(k, file, file.name);
    let before;
    if (reader.isPristine(tab)) {
      body.append("ids", tab.fileId);
      before = tab.size || tab.original?.length || 0;
    } else {
      const bytes = await reader.currentBytes();
      if (!bytes || reader.activeTab !== tab) throw new Error("the document changed: try again");
      before = bytes.length;
      body.append("files", new File([bytes], /\.pdf$/i.test(tab.name) ? tab.name : tab.name + ".pdf", { type: "application/pdf" }));
    }
    const ui = {
      start(id) { item.job = id; item.cancel.hidden = false; item.cancel.disabled = false; item.cancel.textContent = "Cancel"; },
      message(text) { status.textContent = text; },
      progress(fraction) { const pct = Math.round(fraction * 100); item.bar.firstChild.style.width = `${Math.max(4, pct)}%`; item.bar.setAttribute("aria-valuenow", String(pct)); },
      cancelled() { item.cancelled = true; },
      end() { item.job = null; },
    };
    const res = await runJob("/api/run", { method: "POST", body, headers: isDesktop() ? { "X-Pdfm-Keep": "1" } : {} }, ui);
    if (!res) { done.replaceChildren(el("span", {}, "Cancelled: nothing was changed.")); done.hidden = false; return; }
    const name = decodeURIComponent(res.headers.get("X-Pdfm-Filename") || "") || "result.pdf";
    const resultId = res.headers.get("X-Pdfm-Result-Id");
    const notes = decodeURIComponent(res.headers.get("X-Pdfm-Note") || "").split("\n").filter(Boolean);
    const blob = await res.blob();
    if (/\.pdf$/i.test(name)) {
      const nb = (text) => text.replace(" ", "\u00a0");  // "7.0 KB" stays on one line
      const sizes = `${nb(humanSize(before))} → ${nb(humanSize(blob.size))}`;
      const pct = before ? Math.round((1 - blob.size / before) * 100) : 0;
      const password = op.id === "encrypt" ? (opts.new_password || null) : op.id === "decrypt" ? null : tab.password;
      const made = await reader.openResult(new Uint8Array(await blob.arrayBuffer()), name, password, tab);
      if (!made) { fail("the result couldn't be opened"); return; }
      // Only in the app until it's saved: a dot on its tab, Save… in its note.
      made.made({ op: op.id, label: op.label, from: tab, sizes: pct >= 5 ? `${sizes}, ${pct}% smaller` : sizes, notes });
      if (fp.tab === made) fpShowNote(made);
      done.replaceChildren(icon("check"), el("div", {}, el("b", {}, made.madeBy.sizes), ". Opened beside the original as ", el("b", {}, name), "."));
      done.hidden = false;
      reader.toast(`Opened ${name} in a new tab`, 5000);
    } else {
      if (item.url) URL.revokeObjectURL(item.url);
      item.url = URL.createObjectURL(blob);
      const said = el("div", { class: "fp-said" }, humanSize(blob.size));
      done.replaceChildren(icon("check"), el("div", {}, el("b", {}, name), said,
        ...notes.map((line) => el("div", { class: "fp-line" + (/^Careful\b/.test(line) ? " careful" : "") }, line)),
        el("div", { class: "fp-save" }, ...saveControls(blob, name, resultId, item.url, (...n) => said.replaceChildren(...n),
          { onError: fail, download: "Save" }))));
      done.hidden = false;
    }
  } catch (e) {
    const rawMsg = String(e.message || e);
    if (op.id === "translate" && /language pack|madlad|pack not installed/i.test(rawMsg) && item.box.isConnected) {
      const needed = [...new Set([opts.source_lang, opts.target_lang].filter((c) => c && c !== "en" && c !== "auto"))];
      const packs = needed.length ? needed : ["madlad-400-3b"];
      const btn = el("button", { type: "button", class: "btn seal sm", style: "margin-top:6px;" },
        icon("download"), `Download pack (${packs.join(", ")}) & translate`);
      btn.addEventListener("click", async () => {
        if (item.running) return;
        error.hidden = true;
        item.running = true;
        runBtn.disabled = true; runBtn.classList.add("busy");
        status.textContent = `Downloading language pack (${packs.join(", ")})…`;
        item.bar.firstChild.style.width = "0";
        item.progress.hidden = false;
        const dlUi = {
          start(id) { item.job = id; item.cancel.hidden = false; item.cancel.disabled = false; item.cancel.textContent = "Cancel"; },
          message(text) { status.textContent = text; },
          progress(fraction) { const pct = Math.round(fraction * 100); item.bar.firstChild.style.width = `${Math.max(4, pct)}%`; item.bar.setAttribute("aria-valuenow", String(pct)); },
          cancelled() { item.cancelled = true; },
          end() { item.job = null; },
        };
        let ok = true;
        try {
          for (const code of packs) {
            const dlRes = await runJob("/api/i18n/pack/download", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ code }),
            }, dlUi);
            if (!dlRes) { ok = false; break; }
          }
        } catch (dlErr) {
          ok = false;
          fail(capital(dlErr.message || dlErr));
        } finally {
          item.running = false;
          runBtn.disabled = false; runBtn.classList.remove("busy");
          item.progress.hidden = true;
          item.cancel.hidden = true;
        }
        if (ok) await fpRun(item);
      });
      error.replaceChildren(el("div", {}, capital(rawMsg)), btn);
      error.hidden = false;
    } else {
      fail(capital(rawMsg));
    }
  } finally {
    item.running = false;
    runBtn.disabled = false; runBtn.classList.remove("busy");
    item.progress.hidden = true;
    item.cancel.hidden = true;
  }
}

/** On a tab a tool made: what was done, the way back to the original, and
 *  Save… (the result is only in the app until it's saved), then Show in folder. */
function fpShowNote(tab) {
  const m = tab.madeBy;
  if (!m) { fp.note.replaceChildren(); return; }
  const back = !m.from ? null : reader.tabs.includes(m.from)
    ? el("button", { type: "button", class: "link-btn", onclick: () => reader.showTab(m.from) }, m.from.name) : el("b", {}, m.from.name);
  let finish = null;
  if (tab.unsaved) {
    const save = el("button", { type: "button", class: "btn seal fp-saveas", title: "Save it as a file, starting in the original's folder" },
      icon("download"), "Save…");
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const where = await reader.saveTab(tab, m.from?.fileId || null);
        if (where) { m.savedTo = typeof where === "string" ? where : null; m.saved = true; }
      } finally { save.disabled = false; }
      if (fp.tab === tab && m.saved) { fpShowNote(tab); fp.note.querySelector(".fp-reveal, .fp-saved")?.focus(); }
    });
    finish = el("div", { class: "fp-save" }, save, el("span", { class: "fp-unsaved" }, "Not saved yet"));
  } else if (m.saved) {
    const reveal = m.savedTo
      ? el("button", { type: "button", class: "btn secondary fp-reveal", onclick: () => reader.reveal(m.savedTo) }, icon("folder"), "Show in folder") : null;
    finish = el("div", { class: "fp-save" }, el("div", { class: "fp-saved", tabindex: "-1" }, el("span", { class: "saving" }, "Saved"),
      m.savedTo ? ` to ${m.savedTo}` : ""), reveal);
  }
  const made = back ? [`: ${m.sizes}. Made from `, back, reader.tabs.includes(m.from) ? ", still open in its own tab." : "."]
    : [`: ${m.sizes}, cleaned up and made into this PDF.`];
  const noteLine = (line) => {
    const mOcr = /^(\d+\s+pages?\s+may have mistakes:\s*)([\d,\s]+)$/.exec(line);
    if (!mOcr) return el("div", { class: "fp-line" + (/^Careful\b/.test(line) ? " careful" : "") }, line);
    const nums = mOcr[2].split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => n > 0);
    const parts = [mOcr[1]];
    nums.forEach((n, idx) => {
      if (idx > 0) parts.push(", ");
      parts.push(el("button", {
        type: "button", class: "link-btn",
        title: `Open page ${n} in Check OCR`,
        onclick: () => reader.openCheckOcr(n),
      }, String(n)));
    });
    parts.push(" ", el("button", {
      type: "button", class: "btn secondary",
      style: "margin-left:6px;padding:2px 8px;font-size:12px",
      onclick: () => reader.openCheckOcr(nums[0] || null),
    }, "Check OCR"));
    return el("div", { class: "fp-line" }, ...parts);
  };
  fp.note.replaceChildren(el("div", { class: "fp-done" }, icon("check"), el("div", {},
    el("b", {}, m.label), ...made,
    ...m.notes.map(noteLine), finish)));
}

/** A file the reader can't show: run Repair on it (by its id when it's a
 *  file on this computer) and open what comes out in a new tab beside it,
 *  not saved anywhere yet, like the panel's results. Resolves to what to say
 *  under the buttons when that didn't work, else nothing. */
async function repairInReader(tab) {
  const op = ops.find((o) => o.id === "repair");
  if (!op) return "The Repair tool isn't available.";
  const body = new FormData();
  body.append("op", "repair");
  for (const f of op.fields || []) if (f.kind === "check") body.append(f.key, f.default ? "true" : "false");
  const name = /\.pdf$/i.test(tab.name) ? tab.name : tab.name + ".pdf";
  if (tab.fileId) body.append("ids", tab.fileId);
  else body.append("files", new File([tab.original], name, { type: "application/pdf" }));
  const ui = { start() {}, message() {}, progress() {}, cancelled() {}, end() {} };
  try {
    const res = await runJob("/api/run", { method: "POST", body, headers: isDesktop() ? { "X-Pdfm-Keep": "1" } : {} }, ui);
    if (!res) return "Cancelled: nothing was changed.";
    const made = decodeURIComponent(res.headers.get("X-Pdfm-Filename") || "") || name.replace(/\.pdf$/i, "_repaired.pdf");
    const notes = decodeURIComponent(res.headers.get("X-Pdfm-Note") || "").split("\n").filter(Boolean);
    const bytes = new Uint8Array(await (await res.blob()).arrayBuffer());
    const tabNow = await reader.openResult(bytes, made, null, tab);
    if (!tabNow || tabNow.broken) { if (tabNow) reader.closeTab(tabNow); return "Repair made a file, but it can’t be shown either. The file may be damaged past mending."; }
    tabNow.made({ op: "repair", label: op.label, from: tab, sizes: humanSize(bytes.length).replace(" ", "\u00a0"), notes });
    if (fp.tab === tabNow) fpShowNote(tabNow);
    reader.toast(`Opened ${made} in a new tab`, 5000);
    return "";
  } catch (e) {
    return `Repair couldn’t mend it: ${String(e.message || e).replace(/\.$/, "")}.`;
  }
}

/** What's known of a file the reader can't show (its facts, from the server). */
async function brokenFacts(tab) {
  try {
    if (tab.fileId) return await (await api("/api/facts/" + encodeURIComponent(tab.fileId))).json();
    if (!tab.original) return null;
    const body = new FormData();
    body.append("file", new Blob([tab.original], { type: "application/pdf" }), "file.pdf");
    return await (await api("/api/facts", { method: "POST", body })).json();
  } catch (e) { return null; }
}

/** Open a PDF protected for particular people with a Digital ID: the Open
 *  with Digital ID tool, its unprotected copy shown in a new tab. Returns
 *  "" when that worked, else what to say. */
async function openWithIdInReader(tab, id, password) {
  const op = ops.find((o) => o.id === "decrypt-cert");
  if (!op) return "Opening with a Digital ID isn't available.";
  const body = new FormData();
  body.append("op", "decrypt-cert");
  body.append("pfx_password", password || "");
  body.append("pfx", id, id.name);
  const name = /\.pdf$/i.test(tab.name) ? tab.name : tab.name + ".pdf";
  if (tab.fileId) body.append("ids", tab.fileId);
  else body.append("files", new File([tab.original], name, { type: "application/pdf" }));
  const ui = { start() {}, message() {}, progress() {}, cancelled() {}, end() {} };
  try {
    const res = await runJob("/api/run", { method: "POST", body, headers: isDesktop() ? { "X-Pdfm-Keep": "1" } : {} }, ui);
    if (!res) return "Cancelled.";
    const made = decodeURIComponent(res.headers.get("X-Pdfm-Filename") || "") || name.replace(/\.pdf$/i, "_opened.pdf");
    const notes = decodeURIComponent(res.headers.get("X-Pdfm-Note") || "").split("\n").filter(Boolean);
    const bytes = new Uint8Array(await (await res.blob()).arrayBuffer());
    const tabNow = await reader.openResult(bytes, made, null, tab);
    if (!tabNow || tabNow.broken) { if (tabNow) reader.closeTab(tabNow); return "It opened, but the copy can’t be shown."; }
    tabNow.made({ op: "decrypt-cert", label: op.label, from: tab, sizes: humanSize(bytes.length).replace(" ", "\u00a0"), notes });
    if (fp.tab === tabNow) fpShowNote(tabNow);
    reader.toast(`Opened ${made} in a new tab. It has no protection: save it only where it's safe.`, 7000);
    return "";
  } catch (e) {
    return `${String(e.message || e).replace(/\.$/, "")}.`;
  }
}

/** "<tool>: this document" from the palette: open it in the panel when it runs there. */
function fpChoose(id) {
  const op = ops.find((o) => o.id === id);
  if (!runsInPanel(op) || !reader?.doc) { reader?.editWith(id); return; }
  reader.setPanel(true);
  fpSync();
  let item = fp.items.get(id);
  if (!item) {
    const list = fp.chosen.querySelector(".fp-list");
    for (const old of [...list.children]) fpForget(old.dataset.op);
    list.replaceChildren(fpCard(op, op.label, TAGLINES[id] || "", list));
    fp.chosen.hidden = false;
    item = fp.items.get(id);
  }
  fpExpand(item);
}

// ---- command palette (Ctrl+K) -------------------------------------------------
// Every tool, and what the reader can do with the open document, one search away.
const palette = { items: [], shown: [], at: 0 };
function paletteCommands() {
  const list = [];
  const doc = inReader() && reader && reader.doc;
  if (inReader() && reader) for (const c of reader.commands()) list.push({ ...c, group: doc ? "This document" : "Reader", icon: "reader" });
  for (const op of ops) {
    if (op.id === current?.id && op.id !== "home") continue;
    list.push({ label: op.label, words: `${op.group} ${op.help || ""} ${TAGLINES[op.id] || ""} ${SYNONYMS[op.id] || ""}`, syn: SYNONYMS[op.id] || "", group: "Tools", icon: op.id,
      sub: TAGLINES[op.id] || "", run: () => select(op.id) });
    // (Redact: the reader has its own, "Redact this document": one entry, not two alike.)
    if (doc && op.files === "pdf" && op.id !== "redact") {
      list.push({ label: `${op.label}: this document`, words: `${op.group} ${TAGLINES[op.id] || ""} ${SYNONYMS[op.id] || ""}`, group: "This document", icon: op.id,
        run: () => fpChoose(op.id), later: true });
    }
  }
  list.push({ label: "Keyboard shortcuts", sub: "every key the app knows", words: "help keys shortcuts hotkeys keyboard", group: "Help",
    icon: "keyboard", keys: "?", run: () => openKeys(), later: true });
  return list;
}
// Other words people use for a tool: "shrink" finds Compress.
const SYNONYMS = {
  compress: "shrink smaller reduce size", merge: "join combine append", encrypt: "lock protect secure",
  decrypt: "unlock open remove password", "sign-cert": "sign signature certificate", split: "separate cut divide",
  redact: "hide censor black out", ocr: "recognise recognize scan text searchable", "to-word": "docx word edit",
  search: "find look for folder", rotate: "turn", delete: "remove pages", repair: "fix mend broken damaged",
  scan: "scanner paper camera photo phone digitise digitize feeder flatbed wia",
  "flatten-layers": "layers optional content ocg cad drawing dwg plan blueprint hide",
  translate: "language bilingual spanish chinese french german japanese korean arabic offline ai llm",
  "from-drawing": "cad dxf dwg autocad blueprint plan",
  "to-dxf": "cad dxf vector autocad export",
  "sheet-sets": "cad sheets titleblock drawing set split",
  "compare-sets": "cad sheets revision diff drawing set",
};

// ---- keyboard shortcuts (?, or "shortcuts" in the palette) ------------------------
// The keys the app and the reader really answer to (see the reader's #onKey).
const KEYS = [
  ["Everywhere", [["Ctrl+K", "Commands and tools"], ["/", "Commands and tools"], ["?", "This list"], ["Esc", "Close a menu or dialog"]]],
  ["Tool pages", [["Ctrl+Enter", "Run the tool"]]],
  ["Annotate bar open", [["H", "Highlight"], ["C", "Note"], ["A", "Add text"], ["D", "Draw"], ["S", "Sign"],
    ["Ctrl+Z / Ctrl+Y", "Undo / redo"], ["Delete", "Delete what’s selected"], ["Ctrl+Enter", "Add the note being typed"]]],
  ["Measuring", [["Enter", "Finish an area or a path"], ["Backspace", "Take back a point"], ["Esc", "Stop"]]],
  ["Reader", [["Ctrl+O", "Open a PDF"], ["Ctrl+F", "Find in the document"], ["Ctrl+S", "Save"], ["Ctrl+Shift+S", "Save as"],
    ["Ctrl+P", "Print"], ["N or J", "Next page"], ["P or K", "Previous page"], ["Home / End", "First / last page"],
    ["Ctrl+Plus / Ctrl+Minus", "Zoom in / out"], ["Ctrl+0", "Automatic zoom"], ["R / Shift+R", "Turn the view"],
    ["T", "File panel"], ["E", "Edit text"], ["M", "Measure"]]],
];
function openKeys() {
  if ($("keys-dialog").open) return;
  const mac = isMac, say = (k) => (mac ? k.replace(/Ctrl\+/g, "⌘") : k);
  $("keys-body").replaceChildren(...KEYS.map(([title, rows]) => el("section", {}, el("h3", {}, title),
    el("dl", {}, ...rows.flatMap(([k, what]) => [el("dt", {}, ...say(k).split(/( or | \/ )/).map((part) => / or | \/ /.test(part) ? part : el("kbd", {}, part))), el("dd", {}, what)])))));
  $("keys-body").style.removeProperty("--keys-key");
  $("keys-dialog").showModal();
  // Every section's descriptions start at the same distance from its keys: the widest keys'.
  const widest = Math.max(0, ...[...$("keys-body").querySelectorAll("dt")].map((dt) => dt.scrollWidth));  // (layout widths: the dialog may be scaling in)
  if (widest) $("keys-body").style.setProperty("--keys-key", Math.ceil(widest) + "px");
  $("keys-close").focus();
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey) return;
  const a = document.activeElement;
  if (/INPUT|SELECT|TEXTAREA/.test(a?.tagName || "") || a?.isContentEditable || document.querySelector("dialog[open]")) return;
  e.preventDefault();
  closeFlyout();
  openKeys();
});
function paletteMatch(query) {
  const q = query.trim().toLowerCase();
  const doc = inReader() && reader && reader.doc;
  const page = doc && q.match(/^(?:page\s*|p\s*|go to\s*)?(\d+)$/);
  const found = [];
  if (page) {
    const n = Number(page[1]);
    if (n >= 1 && n <= reader.doc.numPages) found.push({ label: `Go to page ${n}`, group: "This document", icon: "reader", run: () => reader.goToPage(n), score: -1 });
  }
  const zoom = doc && q.match(/^(?:zoom\s*(?:to\s*)?)?(\d+(?:[.,]\d+)?)\s*%$/);
  if (zoom) {
    const pct = parseFloat(zoom[1].replace(",", "."));
    if (pct >= 10 && pct <= 1000) found.push({ label: `Zoom to ${pct}%`, group: "This document", icon: "reader", run: () => reader.setZoom(pct / 100), score: -1 });
  }
  if (!q) return palette.items.filter((c) => !c.later).slice(0, 40);
  const words = q.split(/\s+/);
  for (const c of palette.items) {
    const label = c.label.toLowerCase(), hay = (label + " " + (c.words || "")).toLowerCase();
    const parts = hay.split(/[^a-z0-9%]+/);
    if (!words.every((w) => parts.some((p) => p.startsWith(w)) || hay.includes(w))) continue;
    // (Another word for the tool, said whole — "shrink" for Compress — counts nearly as much as its name.)
    const synonym = c.syn && c.syn.split(/\s+/).includes(words[0]);
    const score = label.startsWith(q) ? 0 : synonym ? 0.5 : label.split(/\s+/).some((p) => p.startsWith(words[0])) ? 1 : 2;
    found.push({ ...c, score: score + (c.later ? 0.5 : 0) });
  }
  return found.sort((a, b) => a.score - b.score).slice(0, 40);
}
function renderPalette() {
  const list = $("palette-list");
  list.replaceChildren();
  palette.shown = paletteMatch($("palette-input").value);
  palette.at = Math.min(palette.at, Math.max(0, palette.shown.length - 1));
  if (!palette.shown.length) {
    $("palette-input").removeAttribute("aria-activedescendant");
    list.append(el("li", { class: "palette-empty" }, "Nothing matches. Try another word."));
    return;
  }
  const grouped = !$("palette-input").value.trim();
  let group = null;
  palette.shown.forEach((c, i) => {
    if (grouped && c.group !== group) { group = c.group; list.append(el("li", { class: "palette-group", role: "presentation" }, group)); }
    const row = el("li", { class: "palette-item", role: "option", id: `palette-${i}`, "aria-selected": String(i === palette.at),
      onclick: () => runPalette(i), onpointermove: () => { if (palette.at !== i) { palette.at = i; markPalette(); } } },
      c.svg ? Object.assign(el("span", { class: "palette-icon", "aria-hidden": "true" }), { innerHTML: c.svg }) : icon(ICONS[c.icon] ? c.icon : "file"), el("span", { class: "label" }, c.label, c.sub ? el("span", { class: "sub" }, " — " + c.sub) : ""),
      c.keys ? el("kbd", {}, c.keys) : "");
    list.append(row);
  });
  markPalette();
}
function markPalette() {
  document.querySelectorAll(".palette-item").forEach((row) => row.setAttribute("aria-selected", String(row.id === `palette-${palette.at}`)));
  const row = $(`palette-${palette.at}`);
  if (row) { row.scrollIntoView({ block: "nearest" }); $("palette-input").setAttribute("aria-activedescendant", row.id); }
}
function openPalette() {
  if ($("palette").open) return;
  palette.items = paletteCommands();
  palette.at = 0;
  $("palette-input").value = "";
  renderPalette();
  $("palette").showModal();
  $("palette-input").focus();
}
function runPalette(i) {
  const c = palette.shown[i];
  $("palette").close();
  if (c) Promise.resolve(c.run()).catch((e) => console.error(e));
}
$("palette-input").addEventListener("input", () => { palette.at = 0; renderPalette(); });
$("palette-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); palette.at = Math.min(palette.at + 1, palette.shown.length - 1); markPalette(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); palette.at = Math.max(palette.at - 1, 0); markPalette(); }
  else if (e.key === "Enter") { e.preventDefault(); runPalette(palette.at); }
});
$("palette").addEventListener("click", (e) => { if (e.target === $("palette")) $("palette").close(); });  // the backdrop
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
    e.preventDefault(); e.stopPropagation();
    if ($("palette").open) $("palette").close(); else if (!document.querySelector("dialog[open]")) { closeFlyout(); openPalette(); }
  }
}, { capture: true });

// ---- rail and flyout --------------------------------------------------------------
// The rail's groups, by what a tool does: Read, Pages, Improve, Convert and
// Protect (from the group the server gives each tool, with a few moved by
// hand), and every tool is under All. Tools that work on several files or a
// folder (Recipes, the folder tools) are under no rail group: the start
// screen's "Start from several files" and its More list them ("several").
const RAIL_GROUPS = { read: "Read", pages: "Pages", improve: "Improve", convert: "Convert", protect: "Protect", all: "All tools",
  several: "Several files" };
const RAIL_OF_GROUP = { Read: "read", Combine: "pages", Pages: "pages", Layout: "pages", Stamp: "pages",
  Optimize: "improve", Convert: "convert", Security: "protect", Automate: null, Folders: null };
// Archiving sits with protecting; making accessible and flattening improve a file.
const RAIL_OF_TOOL = { pdfa: "protect", "pdfa-check": "protect", tag: "improve", "access-check": "improve", flatten: "improve", "flatten-layers": "improve" };
const railGroup = (op) => (op.id in RAIL_OF_TOOL ? RAIL_OF_TOOL[op.id] : op.group in RAIL_OF_GROUP ? RAIL_OF_GROUP[op.group] : "convert");
// The headings inside a flyout, where a tool's server group would read wrong there.
const SUB_OF_TOOL = { ocr: "Scans", clean: "Scans", tag: "Accessibility", "access-check": "Accessibility", flatten: "Optimize", "flatten-layers": "Drawings",
  pdfa: "Archive", "pdfa-check": "Archive", merge: "Several PDFs", interleave: "Several PDFs", compare: "Several PDFs",
  scan: "Create", "from-images": "Create", "from-office": "Create", "from-drawing": "Create", web: "Create" };
const SEVERAL_TOOLS = ["merge", "interleave", "compare", "compare-sets", "recipe", "organize", "rename", "duplicates", "search"];
const inFlyout = (op, group) => group === "all" || (group === "several" ? SEVERAL_TOOLS.includes(op.id) : railGroup(op) === group);
let flyoutGroup = null;
/** Every tool as a button[data-op] (the rail's index), and the flyout if it's open. */
function renderTools() {
  const button = (op) => el("button", { type: "button", "data-op": op.id, tabindex: "-1", title: op.label,
    "aria-current": current && current.id === op.id ? "true" : "false", onclick: () => select(op.id) }, op.label);
  $("tools").replaceChildren(...ops.filter((o) => o.group).map(button));
  if (flyoutGroup) renderFlyout(flyoutGroup);
}
function renderFlyout(group) {
  const list = $("flyout-list");
  const tools = ops.filter((o) => o.group && inFlyout(o, group));
  const row = (op) => el("button", { type: "button", class: "fly-tool", "data-op": op.id,
    "aria-current": current && current.id === op.id ? "true" : "false", onclick: () => { closeFlyout(); select(op.id); } },
    el("span", { class: "ticon" }, icon(op.id)), el("strong", {}, op.label), el("small", {}, TAGLINES[op.id] || op.group));
  const parts = [];
  if (group === "all") {
    // The tools pinned, then a few used lately, before the full list.
    const pinned = toolsFrom(favTools());
    const recent = toolsFrom(recentTools()).filter((o) => !pinned.includes(o) && o.id !== "reader").slice(0, 3);
    for (const [title, few] of [["Pinned", pinned], ["Recently used", recent]]) {
      if (few.length) parts.push(el("div", { class: "fly-sec" }, title), ...few.map(row));
    }
  }
  // Under each heading in the order the tools come (All keeps the server's groups).
  const subs = new Map();
  for (const op of tools) {
    const sub = group === "all" ? op.group : SUB_OF_TOOL[op.id] || op.group;
    if (!subs.has(sub)) subs.set(sub, []);
    subs.get(sub).push(op);
  }
  for (const [sub, few] of subs) parts.push(el("div", { class: "fly-sec" }, sub), ...few.map(row));
  list.replaceChildren(...parts);
  $("flyout-title").textContent = RAIL_GROUPS[group];
  $("flyout-count").textContent = plural(tools.length, "tool");
  requestAnimationFrame(flyoutCue);
}
/** A list longer than the window scrolls: a fade at its foot says there's more. */
function flyoutCue() {
  const list = $("flyout-list");
  $("flyout").classList.toggle("more-below", list.scrollHeight - list.scrollTop - list.clientHeight > 4);
  $("flyout").classList.toggle("more-above", list.scrollTop > 4);
}
let flyoutOpener = null;
function openFlyout(group, opener = null) {
  flyoutOpener = opener;
  flyoutGroup = group;
  renderFlyout(group);
  const fly = $("flyout");
  fly.hidden = false;
  document.querySelectorAll(".rail-btn[data-group]").forEach((b) => { if (b.dataset.group !== "read") b.setAttribute("aria-expanded", String(b.dataset.group === group)); });
  $("flyout-list").scrollTop = 0;
  const here = fly.querySelector('.fly-tool[aria-current="true"]') || fly.querySelector(".fly-tool");
  if (here) here.focus({ preventScroll: false });
}
function closeFlyout(refocus = false) {
  if (!flyoutGroup) return;
  const was = flyoutGroup;
  flyoutGroup = null;
  $("flyout").hidden = true;
  document.querySelectorAll(".rail-btn[aria-expanded]").forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (refocus) (document.querySelector(`.rail-btn[data-group="${was}"]`) || (flyoutOpener?.isConnected ? flyoutOpener : null))?.focus();
}
$("flyout-list").addEventListener("scroll", flyoutCue, { passive: true });
window.addEventListener("resize", () => { if (flyoutGroup) flyoutCue(); });
function toggleFlyout(group) { if (flyoutGroup === group) closeFlyout(); else openFlyout(group); }
document.querySelectorAll(".rail-btn[data-group]").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.group === "read") { closeFlyout(); select("reader"); } else toggleFlyout(b.dataset.group);
}));
$("brand").addEventListener("click", () => { closeFlyout(); select("home"); });
$("flyout").addEventListener("keydown", (e) => {
  const rows = [...$("flyout").querySelectorAll(".fly-tool")];
  const at = rows.indexOf(document.activeElement);
  const go = (i) => { e.preventDefault(); rows[(i + rows.length) % rows.length]?.focus(); };
  if (e.key === "ArrowDown") go(at + 1);
  else if (e.key === "ArrowUp") go(at < 0 ? rows.length - 1 : at - 1);
  else if (e.key === "Home") go(0);
  else if (e.key === "End") go(rows.length - 1);
});
// The flyout closes on Escape, on choosing a tool and on a click outside it.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && flyoutGroup && !document.querySelector("dialog[open]")) { e.stopImmediatePropagation(); e.preventDefault(); closeFlyout(true); }
}, { capture: true });
document.addEventListener("pointerdown", (e) => {
  if (flyoutGroup && !e.target.closest("#flyout, .rail-btn[data-group], .fp-all")) closeFlyout();
});
const toolsFrom = (ids) => ids.map((id) => ops.find((o) => o.id === id && o.group)).filter(Boolean);
function favTools() { try { return JSON.parse(localStorage.getItem("pdfm-fav-tools") || "[]"); } catch (e) { return []; } }
function setFav(id, on) {
  const list = favTools().filter((t) => t !== id);
  if (on) list.push(id);
  try { localStorage.setItem("pdfm-fav-tools", JSON.stringify(list)); } catch (e) { /* not kept */ }
  renderTools();
  showFav();
  if (inHome()) renderHome();
}
/** The star beside a tool's name: pin it to the top of All (and the start screen). */
function showFav() {
  const btn = $("fav-btn"), op = current;
  btn.hidden = !op || !op.group || op.files === "reader";
  if (btn.hidden) return;
  const on = favTools().includes(op.id);
  btn.setAttribute("aria-pressed", String(on));
  btn.title = on ? "Unpin this tool" : "Pin this tool to the start screen and the top of All";
  btn.setAttribute("aria-label", btn.title);
  btn.replaceChildren(icon("star"), el("span", {}, on ? "Pinned" : "Pin"));
}
$("fav-btn").addEventListener("click", () => setFav(current.id, !favTools().includes(current.id)));
$("find").addEventListener("click", () => { closeFlyout(); openPalette(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)
      && !document.activeElement.isContentEditable && !document.querySelector("dialog[open]")) {
    e.preventDefault();
    closeFlyout();
    openPalette();
  }
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && inScan()) { e.preventDefault(); scanMake(); return; }
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !inReader()) {
    e.preventDefault();
    // A waiting Run button says why when asked from the keyboard (not while a job runs).
    if ($("run").disabled && current && current.files !== "home" && !document.querySelector("#run.busy, #preview.busy")) run(false); else $("run").click();
  }
});

async function select(id) {
  current = ops.find((o) => o.id === id);
  closeFlyout();
  const readerMode = current.files === "reader", homeMode = current.files === "home", scanMode = current.files === "scan";
  document.body.classList.toggle("reader-mode", readerMode);
  document.body.classList.toggle("home-mode", homeMode);
  document.body.classList.toggle("scan-mode", scanMode);
  $("scan-host").hidden = !scanMode;
  const first = !readerMode && !homeMode && dropFirst(current);
  document.body.classList.toggle("drop-first", first);
  document.body.classList.toggle("opts-later", first && !current.url_field);  // (Web page → PDF: an address can do instead of files)
  $("home-host").hidden = !homeMode;
  if (reader && !readerMode) reader.hide();
  try { localStorage.setItem("pdfm-op", id); } catch (e) {}
  if (!homeMode) rememberTool(id);
  showFav();
  document.querySelectorAll(".tools button[data-op]").forEach((b) => b.setAttribute("aria-current", String(b.dataset.op === id)));
  document.querySelectorAll(".rail-btn[data-group]").forEach((b) => b.setAttribute("aria-current",
    String(homeMode ? false : readerMode ? b.dataset.group === "read" : b.dataset.group === railGroup(current))));
  $("brand").setAttribute("aria-current", String(homeMode));
  $("tool-icon").replaceChildren(icon(id));
  $("title").textContent = current.label;
  showHelp(current.help || defaultHelp(current) || (dropFirst(current) ? TAGLINES[id] || "" : ""));
  document.title = homeMode ? "ZPDF" : current.label + " — ZPDF";
  if (homeMode) { renderHome(); return; }
  if (scanMode) { clearMessages(); await renderScan(); return; }
  if (readerMode) {
    await ensureReader();
    reader.show();
    if (reader.doc) document.title = reader.name + " — ZPDF";
    return;
  }
  // Back from the reader to the same tool (e.g. after opening a search
  // result): keep what was typed and the result.
  const keep = id === formOp;
  const fromOp = formOp;
  if (!keep) rememberForm(fromOp);
  formOp = id;
  if (!keep) foldForm(null);
  const folder = current.files === null;
  $("files-card").hidden = folder;
  $("pw-box").hidden = folder || current.files === "images" || current.files === "office" || current.files === "web" || current.files === "drawing";
  const previews = folder && current.preview !== false;
  $("preview").hidden = !previews;
  $("run-label").textContent = current.action || (folder ? "Apply" : "Run");
  $("run").classList.toggle("secondary", previews);
  $("preview").classList.toggle("secondary", !previews);
  $("file-input").accept = { images: "image/*", office: OFFICE_ACCEPT, web: WEB_ACCEPT, drawing: DRAWING_ACCEPT }[current.files] || ".pdf,application/pdf";
  $("drop-hint").textContent = {
    images: "JPG, PNG, TIFF… one page per image, in this order",
    office: "Word, Excel, PowerPoint, OpenDocument, RTF, text or web page files",
    web: "Saved web pages (.html, .mht) — or type an address under Options",
    drawing: "CAD drawings (.dxf, .dwg)",
    pdf2: current.id === "compare" ? "Two PDFs: the original first, then the changed version" : "Two PDFs: front sides first, then back sides",
  }[current.files] || (current.id === "merge" ? "Add PDFs in the order to combine them" : "One or more PDFs");
  if (keep) { syncDropFirst(); return; }
  const want = accepts(current.files);
  items = withoutDuplicates(items.filter((it) => want(it.file)));
  // Files carried over from the last tool are useful, but said, with Clear.
  const from = ops.find((o) => o.id === fromOp);
  carried = items.length && from && from.id !== id && from.files !== null ? { from: from.label, count: items.length } : null;
  renderForm(); recallForm(id); renderFiles(); clearMessages();
}

// What was typed in a folder tool (Search a folder's folder and words, say)
// is there again when you come back to it, until the app is closed: kept in
// the page's memory only, never written anywhere.
const formMemory = new Map();
function rememberForm(opId) {
  const op = ops.find((o) => o.id === opId);
  if (!op || op.files !== null) return;
  const kept = {};
  for (const f of op.fields || []) {
    const input = $("f-" + f.key);
    if (!input || f.kind === "password" || f.kind === "file" || f.kind === "hidden") continue;
    kept[f.key] = input.type === "checkbox" ? input.checked : input.value;
  }
  formMemory.set(opId, kept);
}
function recallForm(opId) {
  const kept = formMemory.get(opId);
  if (!kept) return;
  for (const [key, value] of Object.entries(kept)) {
    const input = $("f-" + key);
    if (!input) continue;
    if (input.type === "checkbox") input.checked = !!value; else input.value = value;
  }
  updateVisibility();
}

/** The same file twice: same name, size and time, or opened from the same place. */
function sameFile(a, b) {
  return a === b || (a.name === b.name && a.size === b.size && a.lastModified === b.lastModified)
    || (!!a.openedId && a.openedId === b.openedId);
}
function withoutDuplicates(list) {
  const out = [];
  for (const it of list) if (!out.some((o) => sameFile(o.file, it.file))) out.push(it);
  return out;
}
let carried = null;  // {from: the tool's name, count}: files kept when switching tools
function showCarried() {
  const line = $("carried");
  line.hidden = !carried;
  if (!carried) { line.replaceChildren(); return; }
  line.replaceChildren(el("span", {}, `${plural(carried.count, "file")} kept from ${carried.from}`),
    el("button", { type: "button", class: "link-btn", onclick: () => { items = []; carried = null; renderFiles(); clearMessages(); $("drop").focus(); } }, "Clear"),
    el("button", { type: "button", class: "chip-btn carried-x", title: "Hide this note", "aria-label": "Hide this note, keep the files",
      onclick: () => { carried = null; showCarried(); $("drop").focus(); } }, icon("x")));
}
/** The tool's help, folded to its first sentence; "More about this" shows the rest. */
function firstSentence(text) {
  const m = /(?<!\b(?:[Ee]\.g|[Ii]\.e|etc|vs|pp?|[Nn]o))[.!?](?=\s+["“(]?[A-Z0-9])/.exec(text);
  return m ? [text.slice(0, m.index + 1), text.slice(m.index + 1).trim()] : [text, ""];
}
function showHelp(text) {
  const [first, rest] = (text || "").length > 150 ? firstSentence(text) : [text || "", ""];
  const more = $("help-more");
  const restEl = rest ? el("span", { class: "help-rest", id: "help-rest" }, " " + rest) : null;
  $("help").replaceChildren(first, restEl || "");
  more.hidden = !rest;
  more.setAttribute("aria-expanded", "false");
  more.textContent = "More about this";
}
$("help-more").addEventListener("click", () => {
  const rest = $("help-rest"), open = !rest.classList.contains("open");
  rest.classList.toggle("open", open);
  $("help-more").setAttribute("aria-expanded", String(open));
  $("help-more").textContent = open ? "Less" : "More about this";
});
function defaultHelp(op) { return op.files === "pdf" ? "Add one or more PDFs. Several files come back together as a ZIP." : ""; }
/** Tools that start from several files, or from files that aren't a PDF
 *  (images, Office files, web pages): their page opens on one big drop area.
 *  (Folder tools start from a folder typed under Options: no files at all.) */
function dropFirst(op) { return !!op && typeof op.files === "string" && !["home", "reader", "scan"].includes(op.files) && (op.files !== "pdf" || (op.min || 1) >= 2); }
const NUMBER_WORDS = ["", "one", "two", "three", "four", "five"];
/** What the big drop area asks for, in the tool's own words. */
function dropText(op) {
  if (op.files === "pdf2") return op.id === "compare" ? "Drop the two versions to compare" : "Drop the two scans to interleave";
  if (op.files === "images") return "Drop photos or scans to turn into a PDF";
  if (op.files === "office") return "Drop Office documents to turn into PDFs";
  if (op.files === "drawing") return "Drop DXF or DWG drawings to turn into PDFs";
  if (op.files === "web") return "Drop saved web pages to turn into PDFs";
  return `Drop ${NUMBER_WORDS[op.min] || op.min} or more PDFs to ${(op.action || op.label).toLowerCase()}`;
}
/** The page's state for the drop-first tools: files or not yet. */
function syncDropFirst() {
  document.body.classList.toggle("has-files", items.length > 0);
  const first = dropFirst(current) && !inReader() && !inHome();
  $("drop-title").textContent = items.length ? "Add more files" : first ? dropText(current) : "Drop files here or click to choose";
  // The keyboard's way to the (hidden) file box: the "Choose files" button where it shows,
  // else the drop area itself, as a button (Enter or Space opens the chooser).
  const chooser = first && !items.length, d = $("drop"), b = $("drop-choose");
  if (chooser) { d.removeAttribute("role"); d.removeAttribute("tabindex"); b.removeAttribute("tabindex"); b.removeAttribute("aria-hidden"); }
  else { d.setAttribute("role", "button"); d.tabIndex = 0; b.tabIndex = -1; b.setAttribute("aria-hidden", "true"); }
}
function isPdf(f) { return f.type === "application/pdf" || /\.pdf$/i.test(f.name); }
const WEB_ACCEPT = ".html,.htm,.xhtml,.mht,.mhtml";
const OFFICE_ACCEPT = ".html,.htm,.xhtml,.mht,.mhtml,.doc,.docx,.docm,.dot,.dotx,.rtf,.odt,.wpd,.txt,.xls,.xlsx,.xlsm,.xlsb,.ods,.csv,.ppt,.pptx,.pptm,.pps,.ppsx,.odp";
const DRAWING_ACCEPT = ".dxf,.dwg";
function isOffice(f) { const ext = (f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase(); return ext && OFFICE_ACCEPT.split(",").includes(ext); }
function isDrawing(f) { const ext = (f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase(); return ext && DRAWING_ACCEPT.split(",").includes(ext); }
function isWeb(f) { const ext = (f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase(); return ext && WEB_ACCEPT.split(",").includes(ext); }
function accepts(kind) { return kind === "images" ? isImage : kind === "office" ? isOffice : kind === "drawing" ? isDrawing : kind === "web" ? isWeb : isPdf; }
function isImage(f) { return f.type.startsWith("image/"); }

