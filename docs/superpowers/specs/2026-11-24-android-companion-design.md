# Track 4 (`v0.73.0`): Android Companion App (Phase 1 Foundation) — Design Spec

## Overview
Establishes ZPDF's touch-first mobile reader/translator foundation and Android Companion App scaffold without breaking desktop privacy or introducing external runtime dependencies:

1. **Installable PWA Shell (`manifest.webmanifest`, `icon.svg`, `sw.js`)**
   - Adds `.webmanifest` MIME type to `pdfmanager/server/common.py:STATIC_TYPES` and serves `/manifest.webmanifest` and `/sw.js` pre-token in `pdfmanager/gui.py`.
   - `sw.js` caches only static UI shell assets (`/static/*`) and never caches `/api/*` document endpoints.
   - `gui.html` includes manifest link, `theme-color`, mobile web app meta tags, and safe Service Worker registration.

2. **Touch-First Mobile Reader & Translator Bar (`pdfmanager/static/mobile-touch.js` + `reader.css`)**
   - Adds swipe-left / swipe-right page turns when not horizontally scrolled, double-tap zoom toggle (`page-width` ↔ `1.5`), and a compact 44px-target mobile bottom dock on narrow/coarse-pointer screens (`Prev`, `Page`, `Next`, `Theme`, `Translate`).

3. **Android Companion Kotlin WebView Scaffold (`mobile/android/`)**
   - Ready-to-build Gradle/Kotlin Android project (`MainActivity.kt`, `AndroidManifest.xml`, `build.gradle.kts`, `README.md`) with WebView file chooser support, PDF `ACTION_VIEW` intent handling, back navigation, and saved companion URL preference.
