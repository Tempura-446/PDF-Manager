/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Lightweight offline-shell service worker for ZPDF Mobile / PWA Companion.
// Caches ONLY static UI assets (/static/*); NEVER caches /api/* user documents.

const CACHE_NAME = "zpdf-shell-v1";
const SHELL_URLS = [
  "/",
  "/manifest.webmanifest",
  "/static/icon.svg",
  "/static/reader.css",
  "/static/app.css",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Never intercept or cache private document/API endpoints.
  if (url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const fetched = fetch(req).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => undefined);
          }
          return res;
        }).catch(() => cached);
        return cached || fetched;
      })
    );
  }
});
