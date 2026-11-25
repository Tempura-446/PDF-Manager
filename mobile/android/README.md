# ZPDF Android Companion App (Phase 1 Foundation)

Touch-first Android WebView companion shell for ZPDF (`v0.73.0`).

## Features
- Wraps ZPDF's touch-first PWA & Reader UI (`/manifest.webmanifest`, `/sw.js`, `mobile-touch.js`).
- Native Android file picker (`onShowFileChooser`) for opening local PDFs and CBZ files.
- Registers an `ACTION_VIEW` intent filter for `application/pdf`.
- Swipe left/right page navigation, double-tap zoom toggle, and floating 44px-target mobile bottom dock (Prev / Next / Reading Theme cycle / Translate page).
- Saved companion server URL preference (defaults to `http://10.0.2.2:8765/` for Android Emulator host loopback).

## Building in Android Studio
1. Open `mobile/android/` in Android Studio (JDK 17, Android SDK 35).
2. Run `./gradlew assembleDebug` or click **Run**.
3. Start ZPDF on your desktop (`zpdf gui --port 8765`) or install the PWA directly from any mobile browser via **Add to Home Screen**.
