# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Phase 2 Text Editing Integrity regression tests (T-001, T-002, T-003)."""
import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pikepdf
import pytest
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, textedit
from pdfmanager.textedit import rewrite


def _make_pdf(path, lines, pagesize=(612, 792), extra_draw=None):
    c = canvas.Canvas(str(path), pagesize=pagesize)
    c.setFont("Helvetica", 14)
    for x, y, text in lines:
        c.drawString(x, y, text)
    if extra_draw:
        extra_draw(c)
    c.showPage()
    c.save()
    return path


def _find_line_rect(path, page_idx=0, target=None):
    import pypdfium2 as pdfium

    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            tp = doc[page_idx].get_textpage()
            try:
                full = tp.get_text_range()
                if target is None:
                    start, end = 0, tp.count_chars()
                else:
                    idx = full.index(target)
                    start, end = idx, idx + len(target)
                boxes = [tp.get_charbox(i, loose=True) for i in range(start, end)
                         if tp.get_text_range(i, 1).strip()]
                return [
                    min(b[0] for b in boxes) - 1,
                    min(b[1] for b in boxes) - 1,
                    max(b[2] for b in boxes) + 1,
                    max(b[3] for b in boxes) + 1,
                ]
            finally:
                tp.close()
        finally:
            doc.close()


# ---- T-001: Ordered character validation -----------------------------------------------

def test_t001_ordered_text_matches_rejects_anagrams_missing_and_inserted_chars():
    # Exact match
    assert rewrite._ordered_text_matches("CONFIDENTIAL", "CONFIDENTIAL")
    # Same character counts (anagram / transposed letters) MUST fail!
    assert not rewrite._ordered_text_matches("CONFIDENTAIL", "CONFIDENTIAL")
    assert not rewrite._ordered_text_matches("LAITNEDIFNOC", "CONFIDENTIAL")
    # Missing characters
    assert not rewrite._ordered_text_matches("CONFIDENTIA", "CONFIDENTIAL")
    # Extra character inserted inside word
    assert not rewrite._ordered_text_matches("CONFIXDENTIAL", "CONFIDENTIAL")
    # Repeated character mismatch
    assert not rewrite._ordered_text_matches("ABBB", "AAB")
    # Whitespace variations and multi-line text
    assert rewrite._ordered_text_matches("Hello\n  World\t2026", "Hello World 2026")
    # Ligatures via NFKC normalization
    assert rewrite._ordered_text_matches("\ufb01le \ufb02ow o\ufb03ce", "file flow office")
    # Soft hyphens & PDFium end-of-line hyphen markers
    assert rewrite._ordered_text_matches("CONFI\u00adDENTIAL", "CONFIDENTIAL")
    assert rewrite._ordered_text_matches("CONFI\ufffeDENTIAL", "CONFIDENTIAL")
    assert rewrite._ordered_text_matches("CONFI-DENTIAL", "CONFIDENTIAL")
    # Bidirectional (RTL) text in both logical and visual (reversed) order
    assert rewrite._ordered_text_matches("\u200fمرحبا", "مرحبا")
    assert rewrite._ordered_text_matches("ابحرم", "مرحبا")
    assert not rewrite._ordered_text_matches("مرحاب", "مرحبا")


def test_t001_intact_rejects_transposed_characters_in_pdf(tmp_path):
    """Verify _intact() actually rejects a PDF where the replacement has the same
    character frequencies as expected ('CONFIDENTAIL' vs 'CONFIDENTIAL')."""
    bad_pdf = _make_pdf(tmp_path / "transposed.pdf", [(72, 700, "CONFIDENTAIL")])
    good_pdf = _make_pdf(tmp_path / "correct.pdf", [(72, 700, "CONFIDENTIAL")])
    expect = {
        0: {
            "letters": [],
            "bands": [(72.0, 700.0, 1.0, 0.0, 14.0)],
            "strict": True,
            "texts": [(0, "CONFIDENTIAL")],
            "touched": [(70.0, 695.0, 220.0, 720.0)],
            "flowed": False,
        }
    }
    assert rewrite._intact(good_pdf, expect) is True
    assert rewrite._intact(bad_pdf, expect) is False


def test_t001_multiple_replacements_and_surrounding_text_intact(tmp_path):
    src = _make_pdf(tmp_path / "multi.pdf", [
        (72, 720, "Header stays intact"),
        (72, 680, "Status: DRAFT"),
        (72, 640, "Owner: Alice"),
        (72, 600, "Footer stays intact"),
    ])
    r1 = _find_line_rect(src, 0, "Status: DRAFT")
    r2 = _find_line_rect(src, 0, "Owner: Alice")
    out = tmp_path / "out.pdf"
    rep = []
    count = textedit.edit_text(
        src, out,
        [
            {"page": 1, "rect": r1, "old": "Status: DRAFT", "text": "Status: FINAL"},
            {"page": 1, "rect": r2, "old": "Owner: Alice", "text": "Owner: Bob"},
        ],
        report=rep,
        require_permanent=True,
    )
    assert count == 2
    summary = textedit.summarize_report(rep)
    assert summary == {"total": 2, "permanent": [1, 2], "covered": [], "all_permanent": True}
    from pypdf import PdfReader

    page_text = PdfReader(str(out)).pages[0].extract_text() or ""
    assert "DRAFT" not in page_text and "Alice" not in page_text
    assert "FINAL" in page_text and "Bob" in page_text
    assert "Header stays intact" in page_text and "Footer stays intact" in page_text


# ---- T-002: Permanent editing vs. visual covering --------------------------------------

def test_t002_require_permanent_refuses_fallback_while_default_allows_cover(tmp_path, monkeypatch):
    src = _make_pdf(tmp_path / "doc.pdf", [(72, 700, "Original Secret")])
    rect = _find_line_rect(src, 0, "Original Secret")
    edits = [{"page": 1, "rect": rect, "old": "Original Secret", "text": "Public Value"}]

    # Simulate direct-rewrite verification failure
    monkeypatch.setattr(rewrite, "_intact", lambda path, expect: False)

    # 1. Default (require_permanent=False): falls back to visual covering and reports it clearly
    out_cov = tmp_path / "covered.pdf"
    rep_cov = []
    n = textedit.edit_text(src, out_cov, edits, report=rep_cov)
    assert n == 1 and out_cov.is_file()
    assert rep_cov[0]["covered"] is True
    assert textedit.summarize_report(rep_cov) == {"total": 1, "permanent": [], "covered": [1], "all_permanent": False}

    # 2. Permanent-only mode (require_permanent=True): refuses fallback and does not write output
    out_perm = tmp_path / "perm.pdf"
    with pytest.raises(core.PdfManagerError, match="permanent text edit could not be completed"):
        textedit.edit_text(src, out_perm, edits, require_permanent=True)
    assert not out_perm.exists()


def test_t002_http_textedit_respects_require_permanent(tmp_path, monkeypatch):
    src = _make_pdf(tmp_path / "http.pdf", [(72, 700, "Secret Line")])
    rect = _find_line_rect(src, 0, "Secret Line")
    edits = [{"page": 1, "rect": rect, "old": "Secret Line", "text": "Changed Line"}]

    monkeypatch.setattr(rewrite, "_intact", lambda path, expect: False)

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token
        boundary = "----zpdftextedit"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="edits"\r\n\r\n{json.dumps(edits)}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="require_permanent"\r\n\r\ntrue\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="http.pdf"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode("utf-8")
            + src.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        req = urllib.request.Request(
            base + "/api/text/edit",
            data=body,
            headers={"X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400
        err_payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert "permanent" in err_payload.get("error", "").lower()
    finally:
        srv.shutdown()
        srv.server_close()


# ---- T-003: Visual fidelity validation -------------------------------------------------

def test_t003_visual_fidelity_passes_clean_edits_and_detects_unexpected_changes(tmp_path):
    # 1. Unmodified page -> identical
    p1 = _make_pdf(tmp_path / "v1.pdf", [(72, 700, "Hello World"), (72, 500, "Untouched Footer")])
    same = textedit.verify_visual_fidelity(p1, p1, {0: []})
    assert same["ok"] is True
    assert same["pages"][0]["changed_ratio"] == 0.0

    # 2. Legitimate text replacement with verify_visual=True -> passes
    rect = _find_line_rect(p1, 0, "Hello World")
    edited = tmp_path / "v1_edited.pdf"
    vrep = {}
    textedit.edit_text(
        p1, edited,
        [{"page": 1, "rect": rect, "old": "Hello World", "text": "Hello ZPDF"}],
        require_permanent=True,
        verify_visual=True,
        visual_report=vrep,
    )
    assert vrep["ok"] is True
    assert vrep["pages"][0]["ok"] is True

    # 3. Unexpected visual change outside the edited region -> detected!
    tampered = _make_pdf(
        tmp_path / "tampered.pdf",
        [(72, 700, "Hello ZPDF"), (72, 500, "Untouched Footer")],
        extra_draw=lambda c: (c.setFillColorRGB(0, 0, 0), c.rect(100, 200, 300, 120, fill=1, stroke=0)),
    )
    bad = textedit.verify_visual_fidelity(p1, tampered, {0: [tuple(rect)]}, dpi=100)
    assert bad["ok"] is False
    assert bad["pages"][0]["ok"] is False
    assert bad["pages"][0]["changed_ratio"] > 0.01

    # 4. Page size mismatch -> detected!
    small_page = _make_pdf(tmp_path / "small.pdf", [(72, 400, "Hello World")], pagesize=(400, 500))
    dim_res = textedit.verify_visual_fidelity(p1, small_page, {0: []})
    assert dim_res["ok"] is False
    assert "dimensions" in dim_res["pages"][0]["reason"]
