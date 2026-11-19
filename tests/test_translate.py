# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Priority #7 Phase 3: Offline PDF translation engine, placeholder protection, and 3 output modes."""
import json
import threading
import urllib.request
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
from reportlab.pdfgen import canvas

from pdfmanager import appdata, core, gui, translate


def _fake_translator(batch):
    """Deterministic offline test translator that reverses alphabetic words while leaving tokens intact."""
    out = []
    for text in batch:
        words = []
        for w in text.split(" "):
            if w.startswith("__ZTOK") and w.endswith("__"):
                words.append(w)
            else:
                words.append(f"[FR:{w}]")
        out.append(" ".join(words))
    return out


def _make_sample_pdf(path, pages_lines, add_note=False):
    raw = path.with_name(path.stem + "_raw.pdf")
    c = canvas.Canvas(str(raw), pagesize=(612, 792))
    for lines in pages_lines:
        y = 720
        for ln in lines:
            c.drawString(72, y, ln)
            y -= 20
        c.showPage()
    c.save()
    if not add_note:
        raw.replace(path)
        return path
    reader = PdfReader(str(raw))
    writer = PdfWriter()
    p0 = writer.add_page(reader.pages[0])
    note = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Text"),
        NameObject("/Rect"): ArrayObject([FloatObject(72), FloatObject(700), FloatObject(92), FloatObject(720)]),
        NameObject("/Contents"): TextStringObject("Please review invoice"),
    })
    p0[NameObject("/Annots")] = ArrayObject([writer._add_object(note)])
    for extra in reader.pages[1:]:
        writer.add_page(extra)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def test_sentence_order_dedup_and_placeholder_protection():
    calls = []

    def tracking_translator(batch):
        calls.append(list(batch))
        return _fake_translator(batch)

    inputs = [
        "Invoice INV-2026-118 for $1,250.00 is due 2026-11-30. Contact billing@acme.test or https://acme.test/pay.",
        "Header Text",
        "Header Text",  # duplicate header must be translated only once!
    ]
    out = translate.translate_texts(inputs, "en", "fr", translator=tracking_translator)
    assert len(out) == 3
    assert "INV-2026-118" in out[0]
    assert "$1,250.00" in out[0]
    assert "2026-11-30" in out[0]
    assert "billing@acme.test" in out[0]
    assert "https://acme.test/pay." in out[0] or "https://acme.test/pay" in out[0]
    assert "[FR:Invoice]" in out[0]
    assert out[1] == out[2]
    # Verify deduplication: "Header Text" sent once across all batches
    sent = [s for batch in calls for s in batch]
    assert sent.count("Header Text") == 1


def test_missing_language_pack_error_names_pack_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "empty_appdata")
    with pytest.raises(core.PdfManagerError, match=r"'fr' language pack.*MB"):
        translate.translate_texts(["Hello world"], "en", "fr")


def test_translate_pdf_all_three_modes_and_annotations(tmp_path):
    src = _make_sample_pdf(
        tmp_path / "invoice.pdf",
        [["Hello world INV-2026-118", "Total 42.50"], ["Second page summary"]],
        add_note=True,
    )

    # 1. Text mode
    out_txt = tmp_path / "out.txt"
    r_txt = translate.translate_pdf(src, out_txt, "en", "fr", mode="text", translator=_fake_translator)
    assert r_txt["pages"] == 2
    txt_content = out_txt.read_text(encoding="utf-8")
    assert "--- Page 1 ---" in txt_content and "INV-2026-118" in txt_content and "[FR:Hello]" in txt_content

    # 2. Side-by-side mode (double width)
    out_sbs = tmp_path / "sbs.pdf"
    r_sbs = translate.translate_pdf(src, out_sbs, "en", "fr", mode="side-by-side", translator=_fake_translator)
    assert r_sbs["pages"] == 2
    sbs_reader = PdfReader(str(out_sbs))
    assert len(sbs_reader.pages) == 2
    assert float(sbs_reader.pages[0].mediabox.width) == pytest.approx(1224.0, abs=1.0)
    assert "INV-2026-118" in (sbs_reader.pages[0].extract_text() or "")

    # 3. In-place mode (replaces text + translates annotation /Contents)
    out_inp = tmp_path / "inplace.pdf"
    r_inp = translate.translate_pdf(src, out_inp, "en", "fr", mode="in-place", translator=_fake_translator)
    assert r_inp["pages"] == 2 and r_inp["annotations_translated"] == 1
    inp_reader = PdfReader(str(out_inp))
    assert "[FR:Hello]" in (inp_reader.pages[0].extract_text() or "")
    annot_obj = inp_reader.pages[0]["/Annots"][0].get_object()
    assert "[FR:Please]" in str(annot_obj["/Contents"])


def test_http_translate_page_endpoint(tmp_path, monkeypatch):
    src = _make_sample_pdf(tmp_path / "page.pdf", [["Hello world 2026-11-19"]])
    monkeypatch.setattr(translate, "_load_offline_translator", lambda s, d: _fake_translator)

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token
        boundary = "----zpdftr"
        parts = []
        for k, v in [("page", "1"), ("from", "en"), ("to", "fr")]:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="page.pdf"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode("utf-8")
            + src.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        req = urllib.request.Request(
            base + "/api/translate/page",
            data=b"".join(parts),
            headers={"X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req) as res:
            assert res.status == 200
            data = json.loads(res.read().decode("utf-8"))
        assert data["page"] == 1 and data["dst_lang"] == "fr"
        assert "[FR:Hello]" in data["translated"] and "2026-11-19" in data["translated"]
    finally:
        srv.shutdown()
        srv.server_close()
