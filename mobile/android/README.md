# ZPDF Android Companion App (Phase 2)

Touch-first Android WebView companion shell for ZPDF (`v0.77.0`).

## Features
- Wraps ZPDF's touch-first PWA & Reader UI (`/manifest.webmanifest`, `/sw.js`, `mobile-touch.js`).
- **Incoming `ACTION_VIEW` PDF handoff**: Tap any `.pdf` on Android and open it directly inside ZPDF Reader via `window.zpdfOpenBase64Pdf`.
- **Offline reconnect screen & `ZpdfAndroid` JS bridge**: Clean native retry + server URL dialog when ZPDF Desktop isn't reachable yet.
- **Manga RTL swipe mode + AI Assistant dock**: Toggle LTR/RTL swipe direction right on the floating mobile bottom dock, plus 1-tap theme cycle, translate page, and Local AI Assistant.
- **Opt-in LAN Companion Mode**: Start ZPDF with `lan=True` (`GET /api/companion/info`) to accept RFC-1918 private LAN connections safely while keeping default desktop mode strictly `127.0.0.1`-only.

## Building in Android Studio
1. Open `mobile/android/` in Android Studio (JDK 17, Android SDK 35).
2. Run `./gradlew assembleDebug` or click **Run**.
3. Start ZPDF on your desktop or install the PWA directly from any mobile browser via **Add to Home Screen**.
