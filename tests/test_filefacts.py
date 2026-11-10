"""What ZPDF can tell about a file at a glance: the facts beside a recent
file and behind the suggestions for an open one."""
import io
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pikepdf
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).parent))
import badpdfs  # noqa: E402

from pdfmanager import core, filefacts  # noqa: E402


def text_pdf(path, pages=3):
    c = canvas.Canvas(str(path))
    for n in range(pages):
        c.drawString(72, 720, f"Page {n + 1}: the quick brown fox")
        c.showPage()
    c.save()
    return Path(path)


def scan_pdf(path, pages=2, words=None):
    """Pages that are one full-page picture; ``words``: an invisible text layer too."""
    buf = io.BytesIO()
    Image.new("L", (1240, 1754), 235).save(buf, "JPEG")
    pdf = pikepdf.new()
    name = pikepdf.Name
    for _ in range(pages):
        picture = pikepdf.Stream(pdf, buf.getvalue(), Type=name.XObject, Subtype=name.Image, Width=1240,
                                 Height=1754, ColorSpace=name.DeviceGray, BitsPerComponent=8, Filter=name.DCTDecode)
        page = pdf.add_blank_page(page_size=(595, 842))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=picture))
        content = b"q 595 0 0 842 0 0 cm /Im Do Q"
        if words:
            page.Resources.Font = pikepdf.Dictionary(F1=pikepdf.Dictionary(
                Type=name.Font, Subtype=name.Type1, BaseFont=name.Helvetica))
            content += b" BT 3 Tr /F1 10 Tf 72 700 Td (" + words.encode() + b") Tj ET"
        page.Contents = pdf.make_stream(content)
    pdf.save(path)
    return Path(path)


def test_an_ordinary_document(tmp_path):
    src = text_pdf(tmp_path / "a.pdf")
    facts = filefacts.facts(src)
    assert facts["pages"] == 3 and facts["size"] == src.stat().st_size
    assert facts["has_text"] and not facts["scanned"] and not facts["protected"]
    assert facts["form_fields"] == 0 and facts["signatures"] == 0 and not facts["tagged"] and not facts["pdfa"]
    assert "error" not in facts


def test_a_scan_with_and_without_its_text(tmp_path):
    bare = filefacts.facts(scan_pdf(tmp_path / "a.pdf"))
    assert bare["scanned"] and not bare["has_text"]
    searchable = filefacts.facts(scan_pdf(tmp_path / "b.pdf", words="recognised words"))
    assert searchable["scanned"] and searchable["has_text"]


def test_a_scan_kept_sharp_by_compress_is_still_a_scan(tmp_path):
    from pdfmanager import scanlayers  # noqa: F401  (the engine must be there)
    sys.path.insert(0, str(Path(__file__).parent))
    import ocrpages
    import test_scanlayers

    src = test_scanlayers.scan(ocrpages.render(), tmp_path / "scan.pdf")
    small = core.compress(src, tmp_path / "small.pdf", level="high")
    assert filefacts.facts(small)["scanned"]


def test_a_protected_file_says_so_and_nothing_else(tmp_path):
    core.encrypt(text_pdf(tmp_path / "a.pdf"), tmp_path / "locked.pdf", "secret")
    facts = filefacts.facts(tmp_path / "locked.pdf")
    assert facts["protected"] and facts["pages"] is None and "error" not in facts
    opened = filefacts.facts(tmp_path / "locked.pdf", password="secret")
    assert opened["protected"] and opened["pages"] == 3


def test_a_form_and_a_tagged_pdfa_file(tmp_path):
    c = canvas.Canvas(str(tmp_path / "form.pdf"))
    c.acroForm.textfield(name="name", x=72, y=700, width=200, height=20)
    c.acroForm.checkbox(name="agree", x=72, y=660, size=14)
    c.showPage()
    c.save()
    assert filefacts.facts(tmp_path / "form.pdf")["form_fields"] == 2
    from pdfmanager import accessibility, archive

    tagged = accessibility.autotag(text_pdf(tmp_path / "a.pdf"), tmp_path / "tagged.pdf")
    assert filefacts.facts(tmp_path / "tagged.pdf")["tagged"] and tagged
    archive.to_pdfa(scan_pdf(tmp_path / "s.pdf"), tmp_path / "pdfa.pdf", engine="builtin")
    assert filefacts.facts(tmp_path / "pdfa.pdf")["pdfa"] == "2B"


def test_a_signed_file(tmp_path):
    pdf = pikepdf.open(text_pdf(tmp_path / "a.pdf"))
    signature = pdf.make_indirect(pikepdf.Dictionary(
        FT=pikepdf.Name.Sig, T=pikepdf.String("Signature1"),
        V=pikepdf.Dictionary(Type=pikepdf.Name.Sig, Filter=pikepdf.Name("/Adobe.PPKLite"))))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=[signature], SigFlags=3)
    pdf.save(tmp_path / "signed.pdf")
    facts = filefacts.facts(tmp_path / "signed.pdf")
    assert facts["signatures"] == 1 and facts["form_fields"] == 0


@pytest.mark.parametrize("name", badpdfs.NAMES)
def test_no_file_breaks_it(tmp_path, name):
    files = badpdfs.build(tmp_path / "bad")
    facts = filefacts.facts(files[name])
    assert isinstance(facts, dict) and "size" in facts
    json.dumps(facts)  # nothing in it that can't go to the page


def test_a_missing_file_is_just_unreadable(tmp_path):
    assert filefacts.facts(tmp_path / "gone.pdf") == {"size": 0, "pages": None, "error": "can't be read"}


def test_facts_are_remembered_until_the_file_changes(tmp_path, monkeypatch):
    src = text_pdf(tmp_path / "a.pdf")
    calls = []
    real = filefacts.facts
    monkeypatch.setattr(filefacts, "facts", lambda path, password=None: calls.append(1) or real(path, password))
    first = filefacts.remembered(src)
    assert filefacts.remembered(src) == first and len(calls) == 1
    text_pdf(src, pages=5)
    assert filefacts.remembered(src)["pages"] == 5 and len(calls) == 2


def test_the_start_screen_gets_facts_with_its_recent_files(tmp_path):
    from pdfmanager import gui

    src = scan_pdf(tmp_path / "scan.pdf")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    try:
        def get(path):
            req = urllib.request.Request(base + path, headers={"X-Pdfm-Token": token})
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read())

        key = srv.RequestHandlerClass.opened.add(src)
        assert get(f"/api/facts/{key}")["scanned"] is True
        gui.RecentFiles.add(src)
        recent = get("/api/recent")["files"]
        assert recent[0]["name"] == "scan.pdf" and recent[0]["facts"]["pages"] == 2
        assert recent[0]["facts"]["scanned"] and not recent[0]["facts"]["has_text"]
    finally:
        srv.shutdown()
        srv.server_close()


# -- what to suggest for a file -----------------------------------------------------------

def ops(facts):
    return [s["op"] for s in filefacts.suggestions(facts)]


def test_suggestions_follow_the_facts():
    base = {"size": 200_000, "pages": 3, "protected": False, "scanned": False, "has_text": True,
            "form_fields": 0, "signatures": 0, "tagged": False, "pdfa": None}
    assert ops(base) == []
    assert ops({**base, "scanned": True, "has_text": False}) == ["ocr"]
    assert ops({**base, "scanned": True, "has_text": False, "size": 4_100_000}) == ["ocr", "compress"]
    assert ops({**base, "size": 9_000_000}) == ["compress"]
    assert ops({**base, "signatures": 2}) == ["verify-sig"]
    assert ops({"size": 10, "pages": None, "protected": True}) == []
    assert ops({"size": 10, "pages": None, "error": "can't be read"}) == ["repair"]
    for found in filefacts.suggestions({**base, "scanned": True, "has_text": False, "size": 4_100_000}):
        assert set(found) == {"op", "title", "why"} and found["title"][0].isupper() and len(found["why"]) > 10


def test_every_suggested_tool_exists():
    from pdfmanager import gui

    known = {op["id"] for op in gui.OPERATIONS}
    base = {"size": 9_000_000, "pages": 3, "scanned": True, "has_text": False, "signatures": 1}
    offered = ops(base) + ops({"size": 1, "pages": None, "error": "can't be read"})
    assert set(offered) == {"ocr", "compress", "verify-sig", "repair"} and set(offered) <= known


def test_facts_and_suggestions_for_a_file_the_page_holds(tmp_path):
    """A file dropped onto the window has no id: the page sends it to be looked at."""
    import uuid

    from pdfmanager import gui

    src = scan_pdf(tmp_path / "scan.pdf")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    try:
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="scan.pdf"\r\n'
                "Content-Type: application/pdf\r\n\r\n").encode() + src.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(base + "/api/facts", data=body, headers={
            "X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=30) as res:
            found = json.loads(res.read())
        assert found["scanned"] and [s["op"] for s in found["suggestions"]] == ["ocr"]
        key = srv.RequestHandlerClass.opened.add(src)
        req = urllib.request.Request(base + f"/api/facts/{key}", headers={"X-Pdfm-Token": token})
        with urllib.request.urlopen(req, timeout=30) as res:
            assert [s["op"] for s in json.loads(res.read())["suggestions"]] == ["ocr"]
    finally:
        srv.shutdown()
        srv.server_close()
