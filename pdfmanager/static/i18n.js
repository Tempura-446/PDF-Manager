/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Lightweight client-side i18n helper: loads the active locale catalog from
// /api/i18n, interpolates {vars}, applies data-i18n attributes across the DOM,
// sets <html lang="..." dir="ltr|rtl">, and formats numbers/dates via Intl.
(function (global) {
  let _lang = "en";
  let _dir = "ltr";
  let _strings = {};

  function getLanguage() { return _lang; }
  function getDirection() { return _dir; }

  /** Set the active catalog and update <html lang="..." dir="ltr|rtl">. */
  function setCatalog({ lang = "en", dir = "ltr", strings = {} } = {}) {
    _lang = lang || "en";
    _dir = dir === "rtl" ? "rtl" : "ltr";
    _strings = strings && typeof strings === "object" ? strings : {};
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.lang = _lang;
      document.documentElement.dir = _dir;
    }
  }

  /** Look up ``key`` in the active catalog and interpolate ``{name}`` placeholders. */
  function t(key, vars = null) {
    const raw = Object.prototype.hasOwnProperty.call(_strings, key) ? _strings[key] : key;
    if (!vars) return raw;
    return String(raw).replace(/\{([a-zA-Z0-9_]+)\}/g, (m, name) =>
      Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : m
    );
  }

  /** Apply translations to all elements with data-i18n* attributes under ``root``. */
  function applyTranslations(root = typeof document !== "undefined" ? document : null) {
    if (!root || !root.querySelectorAll) return;
    for (const el of root.querySelectorAll("[data-i18n]")) {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = t(key);
    }
    for (const attr of ["title", "aria-label", "placeholder"]) {
      const dataAttr = `data-i18n-${attr}`;
      for (const el of root.querySelectorAll(`[${dataAttr}]`)) {
        const key = el.getAttribute(dataAttr);
        if (key) el.setAttribute(attr, t(key));
      }
    }
  }

  /** Locale-aware number formatting via browser Intl (no extra bundle). */
  function formatNumber(value, options = undefined) {
    try { return new Intl.NumberFormat(_lang, options).format(value); } catch (e) { return String(value); }
  }

  /** Locale-aware date formatting via browser Intl (no extra bundle). */
  function formatDate(value, options = undefined) {
    try { return new Intl.DateTimeFormat(_lang, options).format(value instanceof Date ? value : new Date(value)); } catch (e) { return String(value); }
  }

  /** Fetch active catalog from /api/i18n and apply to the document. */
  async function initI18n(tok = "") {
    try {
      const res = await fetch("/api/i18n", { headers: tok ? { "X-Pdfm-Token": tok } : {} });
      if (!res.ok) return null;
      const payload = await res.json();
      setCatalog(payload);
      applyTranslations();
      return payload;
    } catch (e) {
      return null;
    }
  }

  global.ZpdfI18n = {
    load: initI18n,
    initI18n,
    getLanguage,
    getDirection,
    setCatalog,
    t,
    applyTranslations,
    formatNumber,
    formatDate,
  };
})(typeof window !== "undefined" ? window : globalThis);
