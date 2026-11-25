# Track 4 (`v0.73.0`): Android Companion App (Phase 1 Foundation) — Implementation Plan

- [x] **Step 1 (TDD RED)**: Create `tests/test_mobile_companion.py` verifying:
  - `GET /manifest.webmanifest` returns valid JSON with `application/manifest+json`, `name`, `start_url`, `display: "standalone"`, and icons.
  - `GET /sw.js` returns `text/javascript` and explicitly bypasses `/api/` routes.
  - `GET /static/icon.svg` and `GET /static/mobile-touch.js` are served with valid MIME types.
  - `mobile/android/` Kotlin scaffold files exist and contain required manifest/WebView declarations.
- [x] **Step 2 (PWA Server & Static Assets)**: Add `manifest.webmanifest`, `icon.svg`, `sw.js`, `.webmanifest` MIME type in `server/common.py`, pre-token routes in `gui.py`, and `<link rel="manifest">` + SW registration in `gui.html`.
- [x] **Step 3 (Touch-First Mobile Reader Shell)**: Create `pdfmanager/static/mobile-touch.js`, wire into `reader.js`, and add responsive mobile bottom bar styles in `reader.css`.
- [x] **Step 4 (Android Kotlin Companion Scaffold)**: Create `mobile/android/` Gradle/Kotlin WebView project (`MainActivity.kt`, `AndroidManifest.xml`, `build.gradle.kts`, `settings.gradle.kts`, `README.md`).
- [x] **Step 5 (Version Bump & Verification)**: Bump version to `0.73.0`, run full verification suite + `node --check` + cold-startup check.
