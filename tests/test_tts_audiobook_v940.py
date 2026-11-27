# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for v0.94.0 Zero-Cloud Offline TTS, Read-Aloud Player & Audiobook Export."""

import json
import threading
import urllib.request
import wave
from pathlib import Path
from unittest import mock

from reportlab.pdfgen import canvas as rl_canvas

from pdfmanager import gui, tts
from pdfmanager.cli import parser as cli_parser
from pdfmanager.server.operations import catalog


def _make_pdf(path, pages):
    c = rl_canvas.Canvas(str(path))
    for text in pages:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()


def _write_dummy_wav(out_wav, frames=2205):
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * frames)
    return "mock_tts"


def test_clean_text_and_export_audiobook_wav(tmp_path):
    pdf = tmp_path / "story.pdf"
    _make_pdf(pdf, [
        "Chapter one begins. The sun rose over the valley.",
        "Chapter two continues with offline speech.",
    ])

    sents = tts.extract_readable_sentences(pdf)
    assert len(sents) == 3
    assert sents[0]["page"] == 1
    assert sents[2]["page"] == 2

    out_wav = tmp_path / "story.wav"
    with mock.patch("pdfmanager.tts.synthesize_wav", side_effect=lambda txt, dst, **kw: _write_dummy_wav(dst)):
        res = tts.export_audiobook(pdf, out_wav, speed=1.25)

    assert res["pages"] == 2
    assert res["sentences"] == 3
    assert res["backend"] == "mock_tts"
    assert res["duration_sec"] > 0.5
    assert out_wav.is_file()
    with wave.open(str(out_wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 22050
        assert wf.getnframes() > 4410


def test_tts_http_endpoints_and_cli_catalog(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["Hello from ZPDF offline read aloud."])

    server, _url = gui.make_server(port=0, open_paths=(pdf,))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    headers = {"X-Pdfm-Token": server.token, "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tts/status", headers={"X-Pdfm-Token": server.token})
        with urllib.request.urlopen(req, timeout=5) as res:
            info = json.loads(res.read().decode("utf-8"))
            assert "preferred" in info
            assert len(info["voices"]) >= 4

        fid = next(iter(server.RequestHandlerClass.opened.items))
        req2 = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/tts/page-text",
            data=json.dumps({"id": fid, "page": 1}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=5) as res:
            data = json.loads(res.read().decode("utf-8"))
            assert data["page"] == 1
            assert any("read aloud" in s.lower() for s in data["sentences"])
    finally:
        server.shutdown()
        server.server_close()

    p = cli_parser.build_parser()
    a = p.parse_args(["audiobook", "doc.pdf", "-p", "1-3", "--speed", "1.25"])
    assert a.input == "doc.pdf"
    assert a.speed == 1.25
    assert "audiobook" in {op["id"] for op in catalog.OPERATIONS}
    reader_js = (Path(__file__).resolve().parent.parent / "pdfmanager" / "static" / "reader.js").read_text(encoding="utf-8")
    assert "toggleReadAloud" in reader_js
