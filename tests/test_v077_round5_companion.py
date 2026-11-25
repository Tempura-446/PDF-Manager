# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.77.0 OCR, Translation & Manga v5 + Android Companion Phase 2."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent


def test_ocr_filters_repeated_border_and_ruler_noise():
    from pdfmanager.ocr import layout as ocr_layout

    raw = SimpleNamespace(
        boxes=[
            [(10, 10), (200, 10), (200, 28), (10, 28)],
            [(10, 35), (200, 35), (200, 45), (10, 45)],
            [(10, 50), (200, 50), (200, 60), (10, 60)],
            [(10, 70), (200, 70), (200, 90), (10, 90)],
        ],
        txts=["Invoice Summary", "||||||||||", "==========", "Total: $500"],
        scores=[0.95, 0.88, 0.91, 0.96],
    )
    lines = ocr_layout._lines(raw)
    texts = [ln.text for ln in lines]
    assert "Invoice Summary" in texts
    assert "Total: $500" in texts
    assert "||||||||||" not in texts
    assert "==========" not in texts


def test_translation_llm_entity_protection_and_long_neural_chunking():
    from pdfmanager.translate import text as ttext

    # 1. LLM path preserves URLs, reference codes, and glossary terms via sentinels
    seen_llm_inputs = []

    def fake_llm(batch):
        seen_llm_inputs.extend(batch)
        return [s.replace("Visit", "Visite") for s in batch]

    fake_llm._neural = True
    fake_llm._llm = True
    fake_llm._context_window = 600

    res = ttext.translate_texts(
        ["Visit https://zpdf.local for INV-2026-99 and ZPDF Studio."],
        "en", "es",
        translator=fake_llm,
        glossary="ZPDF Studio = ZPDF Estudio",
    )
    assert res[0] == "Visite https://zpdf.local for INV-2026-99 and ZPDF Estudio."
    # Confirm glossary term and ref code were masked before hitting the LLM
    assert all("ZPDF Studio" not in inp and "INV-2026-99" not in inp for inp in seen_llm_inputs)

    # 2. Offline neural path automatically chunks overlong sentences (> 450 chars)
    seen_mt_lens = []

    def fake_mt(batch):
        seen_mt_lens.extend(len(x) for x in batch)
        return batch

    fake_mt._neural = True
    fake_mt._llm = False
    long_text = " ".join(f"Clause {i} specifies compliance requirements for archival PDF delivery." for i in range(1, 20))
    out = ttext.translate_texts([long_text], "en", "fr", translator=fake_mt)
    assert out[0] == long_text
    assert seen_mt_lens and max(seen_mt_lens) <= 460


def test_manga_comic_caps_and_caption_box():
    from pdfmanager.translate import manga

    assert manga._to_comic_caps("Wait... I can't believe it!") == "WAIT... I CAN'T BELIEVE IT!"
    assert manga._to_comic_caps("日本語") == "日本語"

    bubbles = [
        {"x0": 40, "y0": 40, "x1": 220, "y1": 160, "w": 180, "h": 120, "shape": "oval", "bg_rgb": (1, 1, 1), "text_rgb": (0, 0, 0)},
        {"x0": 40, "y0": 200, "x1": 260, "y1": 280, "w": 220, "h": 80, "shape": "rect", "bg_rgb": (1, 1, 1), "text_rgb": (0, 0, 0)},
    ]
    page = manga.make_manga_overlay(
        400, 400, 400, 400, bubbles, ["Let's go!", "Meanwhile, across the city..."], "en", comic_caps=True,
    )
    extracted = page.extract_text() or ""
    assert "LET'S GO!" in extracted
    assert "MEANWHILE" in extracted


def test_companion_lan_host_validation_endpoint_and_android_phase2():
    from pdfmanager import gui
    from pdfmanager.server import common

    # RFC-1918 LAN host validation
    assert common._is_lan_host("192.168.1.42:8765") is True
    assert common._is_lan_host("10.0.2.2:8765") is True
    assert common._is_lan_host("172.16.5.9:8765") is True
    assert common._is_lan_host("my-desktop.local:8765") is True
    assert common._is_lan_host("evil.example.com:8765") is False
    assert common._is_lan_host("8.8.8.8:8765") is False

    # Companion info endpoint
    server, _url = gui.make_server(port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        token = server.token
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/companion/info",
            headers={"X-Pdfm-Token": token},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            info = json.loads(resp.read().decode("utf-8"))
        assert info["lan_enabled"] is False
        assert info["pwa_manifest"] == "/manifest.webmanifest"
        assert info["url"].startswith("http://")
    finally:
        server.shutdown()
        server.server_close()

    # Android Phase 2 bridge + PDF intent handling
    kt = (ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "tempura446" / "zpdf" / "MainActivity.kt").read_text(encoding="utf-8")
    assert "ZpdfAndroid" in kt
    assert "onReceivedError" in kt
    assert "ACTION_VIEW" in kt
