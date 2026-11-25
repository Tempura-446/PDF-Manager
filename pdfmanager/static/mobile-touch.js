/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Touch-first mobile reader & translator enhancements for ZPDF Companion / PWA (Phase 2).
// Provides LTR/RTL manga swipe page turns, double-tap zoom toggle, Android PDF intent
// bridge (window.zpdfOpenBase64Pdf), and a 44px-target floating mobile bottom dock.

import { el, svg } from "./readerkit.js";
import { openAiAssistant } from "./ai-assistant.js";

const THEMES = ["light", "night", "sepia", "eink"];

export function attachMobileTouch(reader) {
  if (!reader?.container || !reader?.root) return;

  // Register offline-shell Service Worker when served over http(s)
  if ("serviceWorker" in navigator && /^https?:$/.test(location.protocol)) {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }

  // Android WebView bridge: lets MainActivity hand an ACTION_VIEW PDF directly to the Reader
  globalThis.zpdfOpenBase64Pdf = async (b64, name = "document.pdf") => {
    try {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      await reader.openBytes(bytes, name);
    } catch (e) {
      reader.toast("Could not open PDF from Android: " + (e.message || e));
    }
  };

  // 1. Floating mobile bottom dock (CSS shows only on <=760px / coarse pointer when a doc is open)
  let rtlSwipe = false;
  const prevBtn = el("button", { type: "button", class: "rd-mob-btn", title: "Previous page", "aria-label": "Previous page", html: svg("up") });
  const pageLbl = el("span", { class: "rd-mob-page" }, "1 / 1");
  const nextBtn = el("button", { type: "button", class: "rd-mob-btn", title: "Next page", "aria-label": "Next page", html: svg("down") });
  const rtlBtn = el("button", { type: "button", class: "rd-mob-btn", title: "Toggle RTL manga swipe direction", "aria-label": "Toggle RTL manga swipe direction", "aria-pressed": "false" }, "LTR");
  const themeBtn = el("button", { type: "button", class: "rd-mob-btn", title: "Cycle reading theme", "aria-label": "Cycle reading theme", html: svg("moon") });
  const transBtn = el("button", { type: "button", class: "rd-mob-btn", title: "Translate this page", "aria-label": "Translate this page", html: svg("edittext") });
  const aiBtn = el("button", { type: "button", class: "rd-mob-btn", title: "Local AI Assistant", "aria-label": "Local AI Assistant", html: svg("command") });

  const dock = el("div", { class: "rd-mobile-dock", hidden: true }, prevBtn, pageLbl, nextBtn, rtlBtn, themeBtn, transBtn, aiBtn);
  reader.root.append(dock);

  const syncDock = () => {
    const has = !!reader.doc && !!reader.pdfViewer?.pagesCount;
    dock.hidden = !has;
    if (!has) return;
    const cur = reader.pdfViewer.currentPageNumber || 1;
    const total = reader.pdfViewer.pagesCount || 1;
    pageLbl.textContent = `${cur} / ${total}`;
    prevBtn.disabled = cur <= 1;
    nextBtn.disabled = cur >= total;
  };

  prevBtn.addEventListener("click", () => {
    if (reader.pdfViewer?.currentPageNumber > 1) reader.goToPage(reader.pdfViewer.currentPageNumber - 1);
  });
  nextBtn.addEventListener("click", () => {
    if (reader.pdfViewer && reader.pdfViewer.currentPageNumber < reader.pdfViewer.pagesCount) {
      reader.goToPage(reader.pdfViewer.currentPageNumber + 1);
    }
  });
  rtlBtn.addEventListener("click", () => {
    rtlSwipe = !rtlSwipe;
    rtlBtn.textContent = rtlSwipe ? "RTL" : "LTR";
    rtlBtn.setAttribute("aria-pressed", String(rtlSwipe));
    reader.toast(rtlSwipe ? "Manga RTL swipe enabled (swipe right for next page)." : "Standard LTR swipe enabled.");
  });
  themeBtn.addEventListener("click", () => {
    const cur = reader.pageTheme ? reader.pageTheme() : "light";
    const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
    if (reader.setPageTheme) reader.setPageTheme(next);
    reader.toast(`Reading theme: ${next}`);
  });
  transBtn.addEventListener("click", () => {
    if (reader.translateCurrentPage) reader.translateCurrentPage();
  });
  aiBtn.addEventListener("click", () => {
    openAiAssistant(reader, "ask");
  });

  if (reader.eventBus) {
    reader.eventBus.on("pagesinit", syncDock);
    reader.eventBus.on("pagechanging", syncDock);
  }
  reader._syncMobileDock = syncDock;

  // 2. Touch swipe left/right to turn pages (LTR or RTL manga mode) + double-tap zoom toggle
  let startX = 0;
  let startY = 0;
  let startTime = 0;
  let lastTap = 0;

  reader.container.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    startTime = Date.now();
  }, { passive: true });

  reader.container.addEventListener("touchend", (e) => {
    if (!reader.doc || !reader.pdfViewer || e.changedTouches.length !== 1) return;
    const dx = e.changedTouches[0].clientX - startX;
    const dy = e.changedTouches[0].clientY - startY;
    const dt = Date.now() - startTime;

    // Horizontal swipe (only when not scrolled horizontally, so zoomed panning isn't hijacked)
    const canSwipe = reader.container.scrollWidth <= reader.container.clientWidth + 8;
    if (canSwipe && dt < 450 && Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.8) {
      const goNext = rtlSwipe ? (dx > 0) : (dx < 0);
      if (goNext && reader.pdfViewer.currentPageNumber < reader.pdfViewer.pagesCount) {
        reader.goToPage(reader.pdfViewer.currentPageNumber + 1);
      } else if (!goNext && reader.pdfViewer.currentPageNumber > 1) {
        reader.goToPage(reader.pdfViewer.currentPageNumber - 1);
      }
      return;
    }

    // Double-tap zoom toggle (fit width <-> 150%)
    if (dt < 260 && Math.abs(dx) < 18 && Math.abs(dy) < 18) {
      const now = Date.now();
      if (now - lastTap < 320) {
        lastTap = 0;
        const cur = reader.pdfViewer.currentScaleValue;
        reader.pdfViewer.currentScaleValue = cur === "page-width" ? "1.5" : "page-width";
      } else {
        lastTap = now;
      }
    }
  }, { passive: true });
}
