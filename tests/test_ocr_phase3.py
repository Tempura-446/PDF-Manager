# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Better OCR Phase 3: OCR report, private unsure-word markers,
and in-place correction in the reader without altering drawn pixels."""
import io
import json
import threading
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw
import pypdfium2 as pdfium
from pypdf import PdfReader

from pdfmanager import archive, core, gui, ocr, textlayer
from pdfmanager.textlayer import Word


def _scan_pdf(path, pages_text=None):
    """Create a PDF of scanned-image pages (no selectable text)."""
    pages_text = pages_text or ["Invoice 1042"]
    imgs = []
    for i, label in enumerate(pages_text):
        im = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(im)
        draw.rectangle([40, 40, 560, 120], outline="black", width=3)
        draw.text((60, 65), label, fill="black")
        p = path.parent / f"_scan_{i}.png"
        im.save(p)
        imgs.append(p)
    core.images_to_pdf(imgs, path)
    return path


def _text_pdf(path, text="Already digital text on this page."):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=(400, 600))
    c.drawString(50, 500, text)
    c.showPage()
    c.save()
    return path


def _render_png_bytes(pdf_path, page_index=0, scale=1.5):
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            pil = doc[page_index].render(scale=scale).to_pil()
        finally:
            doc.close()
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()


def _multipart(fields, files):
    boundary = "----zpdfTestBoundary7MA4YWxkTrZu0gW"
    parts = []
    for k, v in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
            f"{v}\r\n".encode("utf-8")
        )
    for field, filename, data in files:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode("utf-8")
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


def test_unsure_words_recorded_and_corrected_pixel_identical(tmp_path):
    """Correcting one unsure word updates Find/Copy, removes its unsure marker,
    and leaves every drawn pixel identical."""
    src = _scan_pdf(tmp_path / "scan.pdf", ["Invoice 1042"])
    with_ocr = tmp_path / "with_ocr.pdf"
    fixed = tmp_path / "fixed.pdf"

    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=str(src))
    page = writer.pages[0]
    words = [
        Word("Invoice", [(60, 60), (200, 60), (200, 95), (60, 95)], 0.95),
        Word("l042", [(220, 60), (310, 60), (310, 95), (220, 95)], 0.45),
        Word("Total", [(330, 60), (420, 60), (420, 95), (330, 95)], 0.52),
    ]
    textlayer.add_text_layer(writer, page, words, (600, 800), 72.0)
    added = textlayer.record_unsure_words(writer, page, words, (600, 800), 72.0, base_index=0)
    assert added == 2  # "l042" (0.45) and "Total" (0.52); "Invoice" (0.95) is sure
    writer.write(str(with_ocr))

    # Unsure metadata is readable via read_unsure_words.
    unsure = textlayer.read_unsure_words(with_ocr)
    assert unsure["total"] == 2
    assert [w["text"] for w in unsure["pages"]["1"]] == ["l042", "Total"]
    assert [w["id"] for w in unsure["pages"]["1"]] == [1, 2]

    # Before correction: Find/Copy gives "l042", not "1042".
    before_text = core.extract_text(with_ocr)
    assert "l042" in before_text
    assert "1042" not in before_text
    before_png = _render_png_bytes(with_ocr)

    # Correct "l042" -> "1042".
    res = textlayer.correct_words(with_ocr, fixed, [{"page": 1, "id": 1, "text": "1042"}])
    assert res == fixed

    # After correction: Find and Copy both give "1042".
    after_text = core.extract_text(fixed)
    assert "1042" in after_text
    assert "l042" not in after_text
    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(fixed))
        try:
            tp = doc[0].get_textpage()
            try:
                copied = tp.get_text_range()
                assert "1042" in copied
                assert "l042" not in copied
                searcher = tp.search("1042")
                assert searcher.get_next() is not None
            finally:
                tp.close()
        finally:
            doc.close()

    # Pixel-identical page: nothing drawn changed.
    after_png = _render_png_bytes(fixed)
    assert before_png == after_png

    # Only the remaining unsure word ("Total") is still marked.
    remaining = textlayer.read_unsure_words(fixed)
    assert remaining["total"] == 1
    assert [w["text"] for w in remaining["pages"]["1"]] == ["Total"]

    # Accepting the remaining word clears /OCRUnsure without altering text or pixels.
    accepted = tmp_path / "accepted.pdf"
    res2 = textlayer.correct_words(fixed, accepted, [{"page": 1, "id": 2, "accept": True}])
    assert res2 == accepted
    assert textlayer.read_unsure_words(accepted) == {"pages": {}, "total": 0}
    assert "Total" in core.extract_text(accepted)
    assert _render_png_bytes(accepted) == before_png


def test_unsure_pieceinfo_stripped_by_sanitise(tmp_path):
    """Private /PieceInfo /ZPDF /OCRUnsure is stripped when the PDF is sanitised."""
    src = _scan_pdf(tmp_path / "scan.pdf", ["Memo"])
    with_ocr = tmp_path / "with_ocr.pdf"
    cleaned = tmp_path / "cleaned.pdf"

    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=str(src))
    words = [Word("Merno", [(60, 60), (180, 60), (180, 95), (60, 95)], 0.42)]
    textlayer.add_text_layer(writer, writer.pages[0], words, (600, 800), 72.0)
    textlayer.record_unsure_words(writer, writer.pages[0], words, (600, 800), 72.0)
    writer.write(str(with_ocr))
    assert textlayer.read_unsure_words(with_ocr)["total"] == 1

    archive.sanitize(with_ocr, cleaned)
    assert textlayer.read_unsure_words(cleaned) == {"pages": {}, "total": 0}
    assert "/PieceInfo" not in PdfReader(str(cleaned)).pages[0]


def test_format_report_and_run_builtin_report(tmp_path, monkeypatch):
    """_run_builtin populates report with pages read, skipped reasons, and flagged pages."""
    p_text = _text_pdf(tmp_path / "p1.pdf", "Digital text page one.")
    p_scan_unsure = _scan_pdf(tmp_path / "p2.pdf", ["Page two scan"])
    p_scan_blank = _scan_pdf(tmp_path / "p3.pdf", [""])
    merged = tmp_path / "merged.pdf"
    core.merge([p_text, p_scan_unsure, p_scan_blank], merged)

    call_n = {"n": 0}

    def fake_recognise(image, script=None, quality="standard"):
        call_n["n"] += 1
        if call_n["n"] == 1:
            # Page 2: 4 words, 3 unsure -> flagged!
            ws = [
                Word("Alpha", [(40, 40), (140, 40), (140, 80), (40, 80)], 0.92),
                Word("B3ta", [(150, 40), (240, 40), (240, 80), (150, 80)], 0.44),
                Word("Gamm4", [(250, 40), (360, 40), (360, 80), (250, 80)], 0.48),
                Word("D3lta", [(370, 40), (460, 40), (460, 80), (370, 80)], 0.51),
            ]
            return [ocr.Line("Alpha B3ta Gamm4 D3lta", [(40, 40), (460, 40), (460, 80), (40, 80)], 0.58, ws)]
        # Page 3: no text found
        return []

    monkeypatch.setattr(ocr, "recognise", fake_recognise)
    out = tmp_path / "out.pdf"
    report = {}
    ocr._run_builtin(merged, out, force=False, deskew=False, rotate=False, report=report)

    assert report["pages_read"] == [2]
    assert report["skipped"] == {"already had text": [1], "no text found": [3]}
    assert report["flagged_pages"] == [2]
    assert report["unsure_pages"] == {2: 3}

    lines = ocr.format_report(report)
    assert lines[0] == "Read 1 page; skipped 2 pages (already had text: 1; no text found: 3)."
    assert lines[1] == "1 page may have mistakes: 2"

    # Unsure words were persisted into page 2's private /PieceInfo.
    unsure = textlayer.read_unsure_words(out)
    assert unsure["total"] == 3
    assert list(unsure["pages"].keys()) == ["2"]


def test_gui_ocr_unsure_and_fix_endpoints(tmp_path):
    """Live HTTP round-trip for POST /api/ocr/unsure and POST /api/ocr/fix."""
    src = _scan_pdf(tmp_path / "scan.pdf", ["Hello World"])
    with_ocr = tmp_path / "with_ocr.pdf"

    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=str(src))
    words = [
        Word("He1lo", [(60, 60), (180, 60), (180, 95), (60, 95)], 0.41),
        Word("World", [(200, 60), (320, 60), (320, 95), (200, 95)], 0.96),
    ]
    textlayer.add_text_layer(writer, writer.pages[0], words, (600, 800), 72.0)
    textlayer.record_unsure_words(writer, writer.pages[0], words, (600, 800), 72.0)
    writer.write(str(with_ocr))
    before_png = _render_png_bytes(with_ocr)

    server, _url = gui.make_server(port=0)
    token = server.RequestHandlerClass.token
    base = f"http://127.0.0.1:{server.server_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        pdf_bytes = with_ocr.read_bytes()

        # 1) Query unsure words
        ctype, body = _multipart({}, [("file", "scan.pdf", pdf_bytes)])
        req = urllib.request.Request(
            f"{base}/api/ocr/unsure",
            data=body,
            headers={"Content-Type": ctype, "X-Pdfm-Token": token},
            method="POST",
        )
        with opener.open(req, timeout=15) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
        assert data["total"] == 1
        assert data["pages"]["1"][0]["text"] == "He1lo"
        assert data["pages"]["1"][0]["id"] == 0

        # 2) Fix the unsure word
        ctype, body = _multipart(
            {"edits": json.dumps([{"page": 1, "id": 0, "text": "Hello"}])},
            [("file", "scan.pdf", pdf_bytes)],
        )
        req = urllib.request.Request(
            f"{base}/api/ocr/fix",
            data=body,
            headers={"Content-Type": ctype, "X-Pdfm-Token": token},
            method="POST",
        )
        with opener.open(req, timeout=15) as resp:
            assert resp.status == 200
            assert resp.headers.get("X-Pdfm-Changed") == "1"
            fixed_bytes = resp.read()

        fixed_path = tmp_path / "fixed_api.pdf"
        fixed_path.write_bytes(fixed_bytes)
        assert "Hello" in core.extract_text(fixed_path)
        assert "He1lo" not in core.extract_text(fixed_path)
        assert textlayer.read_unsure_words(fixed_path) == {"pages": {}, "total": 0}
        assert _render_png_bytes(fixed_path) == before_png
    finally:
        server.shutdown()
        server.server_close()
