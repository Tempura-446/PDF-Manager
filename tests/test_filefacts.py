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


def test_a_designed_page_with_a_big_photo_is_not_a_scan(tmp_path):
    """Found on Windows: a picture-heavy guide and a brochure were called
    "scanned". Their text is printed for people to read; a scan's OCR text
    is invisible."""
    designed = scan_pdf(tmp_path / "a.pdf", words="Hudson Beige")
    with pikepdf.open(designed, allow_overwriting_input=True) as pdf:
        for page in pdf.pages:
            page.Contents = pdf.make_stream(page.Contents.read_bytes().replace(b"BT 3 Tr", b"BT 0 Tr"))
        pdf.save(designed)
    found = filefacts.facts(designed)
    assert found["has_text"] and not found["scanned"]
    assert "Scanned pages" not in " ".join(s["why"] for s in filefacts.suggestions(found) if "why" in s)
    restored = scan_pdf(tmp_path / "b.pdf", words="q 3 Tr Q")  # a mode set and undone inside q/Q
    assert filefacts.facts(restored)["scanned"]


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


# -- found in the real window (Windows check of 0.19.0) -----------------------------------

def test_a_scan_with_no_text_isnt_taken_for_a_form_at_a_glance(tmp_path, monkeypatch):
    """The reader's quick look for a form to make fillable: on a scan nobody
    can type in yet, the lines it finds (a contents page's dot leaders) aren't a form."""
    import uuid

    from pdfmanager import formdetect, gui

    monkeypatch.setattr(formdetect, "detect", lambda *a, **k: {1: [{"type": "text", "rect": [1, 2, 3, 4]}]})
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")

    def detect(path, quick):
        boundary = uuid.uuid4().hex
        body = b""
        if quick:
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="pages"\r\n\r\n1-2\r\n'.encode()
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
                 "Content-Type: application/pdf\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(base + "/api/form/detect", data=body, headers={
            "X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read())["fields"]

    try:
        scan, plain = scan_pdf(tmp_path / "scan.pdf"), text_pdf(tmp_path / "plain.pdf")
        assert detect(scan, quick=True) == {}, "no form banner on a bare scan"
        searchable = scan_pdf(tmp_path / "ocr.pdf", words="Contents .......... 3")
        assert detect(searchable, quick=True) == {}, "nor once its text has been recognised"
        assert detect(plain, quick=True) != {}
        assert detect(scan, quick=False) != {}, "asked for outright (Add form fields), it still looks"
    finally:
        srv.shutdown()
        srv.server_close()


def test_bringing_the_window_forward_on_windows_never_waits_on_the_window(monkeypatch):
    """A file handed to the running app froze its window when the server
    thread called into the window's own thread. On Windows the window is
    raised through the system instead."""
    from pdfmanager import gui

    called = []

    class Window:
        title = "ZPDF"

        def __getattr__(self, name):
            called.append(name)
            raise AssertionError(f"window.{name} used from the server's thread")

    monkeypatch.setattr(gui.sys, "platform", "win32")
    monkeypatch.setattr(gui, "_win32_bring_forward", lambda title: called.append(("system", title)) or True)
    gui._raise_window(Window())
    assert called == [("system", "ZPDF")]


# -- stage 3 ---------------------------------------------------------------------------------

def test_a_file_with_a_place_to_sign_is_offered_signing(tmp_path):
    pdf = pikepdf.open(text_pdf(tmp_path / "a.pdf"))
    empty = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Sig, T=pikepdf.String("Signature1")))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=[empty])
    pdf.save(tmp_path / "to-sign.pdf")
    found = filefacts.facts(tmp_path / "to-sign.pdf")
    assert found["to_sign"] == 1 and found["signatures"] == 0 and found["form_fields"] == 0
    assert [s["op"] for s in filefacts.suggestions(found)] == ["sign-cert"]
    assert filefacts.facts(text_pdf(tmp_path / "plain.pdf"))["to_sign"] == 0
    # Signed already: checking comes before signing again.
    assert ops({"size": 1, "pages": 1, "signatures": 1, "to_sign": 1}) == ["verify-sig", "sign-cert"]


def test_a_handed_over_file_lets_the_running_app_come_forward(monkeypatch, tmp_path):
    """Windows only lets a program take the foreground when the one in front
    allows it: the launch that hands a file over says so first."""
    from pdfmanager import gui

    order = []
    monkeypatch.setattr(gui, "_allow_foreground", lambda: order.append("allowed"))
    monkeypatch.setattr(gui, "_instance_file", lambda: tmp_path / "instance.json")
    (tmp_path / "instance.json").write_text(json.dumps({"port": 9, "secret": "s"}), encoding="utf-8")

    class Opener:
        def open(self, request, timeout=0):
            order.append("asked")
            raise OSError("nothing is listening")

    monkeypatch.setattr(gui.urllib.request, "build_opener", lambda *a: Opener())
    assert gui.forward_to_running_app([tmp_path / "a.pdf"]) is False
    assert order == ["allowed", "asked"]


def test_boxes_inside_a_picture_are_no_form_at_a_glance(tmp_path):
    """Found on Windows: a software guide full of screenshots and a photo
    brochure were offered "Make it fillable" for boxes the quick look found
    inside their pictures. A form drawn on the page is still found."""
    from PIL import ImageDraw

    from pdfmanager import formdetect

    shot = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(shot)
    for row in range(5):
        draw.text((40, 60 + row * 100), f"Setting {row + 1}", fill="black")
        draw.rectangle((300, 50 + row * 100, 820, 90 + row * 100), outline="black", width=3)
        draw.rectangle((850, 55 + row * 100, 880, 85 + row * 100), outline="black", width=3)
    shot.save(tmp_path / "shot.png")
    c = canvas.Canvas(str(tmp_path / "guide.pdf"))
    c.drawString(72, 780, "Open the settings window and choose your options.")
    c.drawImage(str(tmp_path / "shot.png"), 72, 380, width=450, height=300)
    c.showPage()
    c.drawString(72, 780, "Name:")
    c.rect(120, 772, 250, 20)
    c.drawString(72, 740, "Address:")
    c.rect(130, 732, 300, 20)
    c.drawString(72, 700, "Phone:")
    c.rect(120, 692, 160, 20)
    c.save()
    found = formdetect.detect(tmp_path / "guide.pdf", pages=[1, 2], quick=True)
    assert 1 not in found, found.get(1)
    # A cover: a photo over most of the page, printed text on it (found on
    # Windows: such a photo was let off as if it were the page's scan).
    big = shot.resize((1200, 1600))
    big.save(tmp_path / "cover.png")
    c = canvas.Canvas(str(tmp_path / "cover.pdf"))
    c.drawImage(str(tmp_path / "cover.png"), 0, 60, width=595, height=760)
    c.drawString(72, 30, "ProSelect 2026 collection")
    c.save()
    assert formdetect.detect(tmp_path / "cover.pdf", pages=[1], quick=True) == {}
    assert len(found.get(2, [])) >= 3, "a form drawn on the page is still a form"


def test_stripe_art_and_letter_outlines_are_no_form_at_a_glance(tmp_path):
    """Found on Windows: a cover with slanted stripes and outlined letters was
    offered as a form: slivers beside the letters and a staircase of "boxes"
    in the stripes. The quick look leaves them out; Add form fields (asked
    for) still looks at everything, and a flat form is still found."""
    from pdfmanager import formdetect

    c = canvas.Canvas(str(tmp_path / "cover.pdf"))
    c.setLineWidth(1.2)
    for i in range(8):  # a staircase of stripes, all ending at the page's edge
        c.rect(466 + i * 13, 100 + i * 21, 595 - (466 + i * 13) - 0.5, 14)
    for i in range(6):  # slanted stripes drawn as filled parallelograms
        p = c.beginPath()
        x = 60 + i * 30
        p.moveTo(x, 60)
        p.lineTo(x + 14, 60)
        p.lineTo(x + 114, 260)
        p.lineTo(x + 100, 260)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    t = c.beginText(60, 520)  # big letters drawn as outlines
    t.setTextRenderMode(1)
    t.setFont("Helvetica-Bold", 120)
    t.textLine("A QUGH")
    c.drawText(t)
    c.setFont("Helvetica", 14)
    c.drawString(60, 760, "A quarterly guide")
    c.drawString(60, 380, "A quiet guide to good things")
    c.save()
    full = formdetect.detect(tmp_path / "cover.pdf")
    assert sum(1 for f in full.get(1, []) if abs(f["rect"][2] - 594) <= 1) >= 3, full  # the staircase, when asked
    assert formdetect.detect(tmp_path / "cover.pdf", quick=True) == {}

    # The boxes measured on Windows: two slivers named from nearby words, a staircase unnamed.
    def box(rect, name="", kind="text"):
        return {"rect": list(rect), "type": kind, "name": name, "border": False}
    measured = [box((300, 600, 308, 631), "A qu"), box((420, 600, 428, 631), "gu")] + [
        box((466 + 13 * i, 145 - 17 * i, 596, 158 - 17 * i)) for i in range(8)]
    assert formdetect._quick_strict(measured) == []
    assert len(formdetect._staircase(measured)) == 8
    # Two labelled fields don't carry the staircase with them.
    kept = formdetect._quick_strict(measured + [box((120, 700, 370, 720), "Name"), box((120, 670, 370, 690), "Phone")])
    assert [f["name"] for f in kept] == ["Name", "Phone"]
    # A checkbox or signature taller than wide isn't a sliver.
    assert len(formdetect._quick_strict([box((0, 0, 10, 30), "Agree", "checkbox"), box((50, 0, 60, 30), "Sign", "signature")])) == 2

    # A flat form whose boxes end at one margin, their left sides stepping with the labels.
    c = canvas.Canvas(str(tmp_path / "form.pdf"))
    for n, label in enumerate(["Name:", "E-mail:", "Address:", "Telephone:"]):
        y = 760 - n * 40
        c.drawString(72, y + 6, label)
        c.rect(72 + c.stringWidth(label) + 8, y, 480 - (c.stringWidth(label) + 8), 20)
    c.rect(72, 560, 12, 12)
    c.drawString(90, 562, "I agree")
    c.save()
    quick = formdetect.detect(tmp_path / "form.pdf", quick=True)
    assert {"Name", "E-mail", "Address", "Telephone"} <= {f["name"] for f in quick.get(1, [])}, quick
