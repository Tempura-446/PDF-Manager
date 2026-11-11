"""Opening a file in the reader without waiting for what isn't needed to
show it: the server looks at a file that is already open by its id (form
detection, signatures) instead of being sent it back, the quick looks are
bounded, and, in a browser (Node's Playwright, skipped where there is none),
a file of a few megabytes comes in pieces and still gets its signature and
form bars, thumbnails are only made when their panel is shown, and Enter
submits the password."""
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pikepdf
import pytest
from reportlab.lib.pagesizes import A0
from reportlab.pdfgen import canvas

from pdfmanager import certsign, core, formdetect, gui
from test_certsign import PFX_PASSWORD, make_id
from test_filepanel import text_pdf
from test_formfind_pdfa import make_form

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "pdfmanager" / "static"
GUI_HTML = HERE.parent / "pdfmanager" / "gui.html"


def padded(path, megabytes=5):
    """``path`` made bigger by bytes that show nowhere (so it's read in pieces)."""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.ZpdfPadding = pdf.make_stream(os.urandom(megabytes << 20))
        pdf.save(path)
    assert path.stat().st_size > megabytes << 20
    return path


@pytest.fixture
def app():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token, srv
    srv.shutdown()
    srv.server_close()


def post(base, token, path, fields, files=(), host=None):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    for name, data in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="file.pdf"\r\n'
                     f"Content-Type: application/pdf\r\n\r\n".encode() + data + b"\r\n")
    headers = {"X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(base + path, data=b"".join(parts) + f"--{boundary}--\r\n".encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


# ---- the server: by id ---------------------------------------------------------------
def test_form_detection_by_id_matches_the_uploaded_copy(app, tmp_path):
    base, token, srv = app
    form = make_form(tmp_path / "form.pdf")
    key = srv.RequestHandlerClass.opened.add(form)
    status, by_id = post(base, token, "/api/form/detect", {"id": key, "pages": "1-2"})
    assert status == 200 and sum(len(v) for v in by_id["fields"].values()) >= 3
    status, uploaded = post(base, token, "/api/form/detect", {"pages": "1-2"}, [("file", form.read_bytes())])
    assert status == 200 and uploaded == by_id
    # (Without "pages" too: the Add form fields editor's question.)
    assert post(base, token, "/api/form/detect", {"id": key})[1] == by_id


def test_signatures_by_id_and_the_quick_look(app, tmp_path):
    base, token, srv = app
    plain = text_pdf(tmp_path / "plain.pdf")
    signed = tmp_path / "signed.pdf"
    certsign.sign(plain, signed, make_id(tmp_path / "jane.pfx")[0], PFX_PASSWORD)
    opened = srv.RequestHandlerClass.opened
    status, found = post(base, token, "/api/sign/verify", {"id": opened.add(signed), "quick": "1"})
    assert status == 200 and [s["signer"] for s in found["signatures"]] == ["Jane Tester"]
    assert found["signatures"][0]["status"] == "valid-untrusted"
    assert post(base, token, "/api/sign/verify", {"id": opened.add(signed)})[1] == found
    # An unsigned file: by id, quick or not, and the uploaded copy with "quick".
    plain_id = opened.add(plain)
    assert post(base, token, "/api/sign/verify", {"id": plain_id, "quick": "1"}) == (200, {"signatures": []})
    assert post(base, token, "/api/sign/verify", {"id": plain_id}) == (200, {"signatures": []})
    assert post(base, token, "/api/sign/verify", {"quick": "1"}, [("file", plain.read_bytes())]) == (200, {"signatures": []})
    assert post(base, token, "/api/sign/verify", {"quick": "1"}, [("file", signed.read_bytes())])[1] == found


def test_the_quick_look_says_when_signatures_cant_be_checked(app, tmp_path, monkeypatch):
    base, token, srv = app
    signed = tmp_path / "signed.pdf"
    certsign.sign(text_pdf(tmp_path / "plain.pdf"), signed, make_id(tmp_path / "jane.pfx")[0], PFX_PASSWORD)
    key = srv.RequestHandlerClass.opened.add(signed)

    def broken(*args, **kwargs):
        raise core.PdfManagerError("the signatures can't be read")

    monkeypatch.setattr(certsign, "verify", broken)
    assert post(base, token, "/api/sign/verify", {"id": key, "quick": "1"}) == (
        200, {"signatures": [], "error": "the signatures can't be read"})
    status, answer = post(base, token, "/api/sign/verify", {"id": key})  # asked for (the details): an error
    assert status == 400 and "can't be read" in answer["error"]


@pytest.mark.parametrize("path", ["/api/form/detect", "/api/sign/verify"])
@pytest.mark.parametrize("key", ["not-an-id", "../../etc/passwd", "/etc/passwd"])
def test_an_id_that_isnt_open_is_refused(app, tmp_path, path, key):
    base, token, _ = app
    status, answer = post(base, token, path, {"id": key, "pages": "1-2", "quick": "1"})
    assert status == 400 and "no longer available" in answer["error"]


@pytest.mark.parametrize("path", ["/api/form/detect", "/api/sign/verify"])
def test_by_id_needs_the_token_and_the_right_host(app, tmp_path, path):
    base, token, srv = app
    key = srv.RequestHandlerClass.opened.add(text_pdf(tmp_path / "plain.pdf"))
    assert post(base, "wrong", path, {"id": key})[0] == 403
    assert post(base, token, path, {"id": key}, host="evil.example")[0] == 403
    assert post(base, token, path, {"id": key})[0] == 200


def test_a_file_that_went_away_is_refused(app, tmp_path):
    base, token, srv = app
    gone = text_pdf(tmp_path / "gone.pdf")
    key = srv.RequestHandlerClass.opened.add(gone)
    gone.unlink()
    for path in ("/api/form/detect", "/api/sign/verify"):
        assert post(base, token, path, {"id": key})[0] == 400


def test_file_mentions_finds_words_across_the_pieces_it_reads(tmp_path):
    for at in (0, (1 << 22) - 5, (1 << 22), (1 << 23) - 1, (1 << 23) + 100):
        data = bytearray(os.urandom(4) * ((1 << 21) + 300))  # 8 MB and a bit
        data = data.replace(b"/ByteRange", b"/ByteRangf")
        assert not gui._file_mentions(_write(tmp_path / "no.bin", data), b"/ByteRange")
        data[at:at + 10] = b"/ByteRange"
        assert gui._file_mentions(_write(tmp_path / "yes.bin", data), b"/ByteRange"), at
    assert not gui._file_mentions(tmp_path / "missing.bin", b"/ByteRange")


def _write(path, data):
    path.write_bytes(data)
    return path


# ---- the quick look at a file just opened is bounded -----------------------------------
def test_the_quick_look_leaves_drawing_sheets_alone(tmp_path, monkeypatch):
    sheet = tmp_path / "plan.pdf"
    c = canvas.Canvas(str(sheet), pagesize=A0)
    for i in range(12):  # boxes and lines, as on a form: but on a sheet nobody fills in
        c.rect(100, 200 + i * 60, 400, 40)
        c.drawString(100, 245 + i * 60, f"Room {i}:")
    c.save()
    assert formdetect.detect(sheet, pages=range(1, 3), quick=True) == {}
    assert formdetect.detect(sheet, pages=range(1, 3))  # asked for (Add form fields): looked at all the same
    # A page of ordinary size with more strokes than any form has.
    form = make_form(tmp_path / "form.pdf")
    assert formdetect.detect(form, quick=True) == formdetect.detect(form)
    monkeypatch.setattr(formdetect, "QUICK_MAX_STROKES", 10)
    assert formdetect.detect(form, quick=True) == {}
    assert formdetect.detect(form)


def test_the_reader_asks_by_id_and_after_the_first_page():
    js = (STATIC / "reader.js").read_text(encoding="utf-8")
    # No whole file turned into text on the page's own thread to look for a signature.
    assert "TextDecoder(\"latin1\").decode(bytes)" not in js
    assert re.search(r"this\.#whenShown\(\(\) => \{ this\.#checkSignatures\(\); this\.#checkForm\(\); \}\)", js)
    # The page starts the file and pdf.js before its stylesheets, and only when started with a file.
    html = GUI_HTML.read_text(encoding="utf-8")
    head = html[:html.index("</head>")] if "</head>" in html else html[:html.index("<body")]
    assert head.index("window.zpdfEarly") < head.index('<link rel="stylesheet"')
    first = int(re.search(r"const FIRST_BYTES = (\d+) \* 1024 \* 1024;", js).group(1)) << 20
    assert f'Range: "bytes=0-{first - 1}"' in head


# ---- in a browser ------------------------------------------------------------------------
def test_opening_files_in_a_browser(app, tmp_path):
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=tmp_path).returncode:
        pytest.skip("needs Node.js with Playwright")
    base, token, srv = app
    opened = srv.RequestHandlerClass.opened
    signed = tmp_path / "Signed contract.pdf"
    certsign.sign(padded(text_pdf(tmp_path / "contract.pdf")), signed, make_id(tmp_path / "jane.pfx")[0], PFX_PASSWORD)
    form = padded(make_form(tmp_path / "Application form.pdf"))
    locked = tmp_path / "Locked.pdf"
    with pikepdf.open(text_pdf(tmp_path / "open.pdf", pages=4)) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(user="open-me", owner="owner"))
    long = text_pdf(tmp_path / "Long report.pdf", pages=120)
    small = text_pdf(tmp_path / "Small.pdf", pages=2)
    ids = [opened.add(p) for p in (signed, form, locked, long, small)]
    run = subprocess.run([node, str(HERE / "readeropen.js"), f"{base}/#token={token}", *ids, "open-me"],
                         capture_output=True, text=True, encoding="utf-8", timeout=420)
    if run.returncode and "Executable doesn't exist" in run.stderr:
        pytest.skip("Playwright's browser isn't installed")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert not out["errors"], out["errors"]

    # (a) A signed file of 5 MB: one first piece, the rest as needed, never the whole file; nothing
    # is sent back to the server; the signature bar shows all the same.
    s = out["signed"]
    size = signed.stat().st_size
    assert s["size"] == size and s["held"] is False
    assert s["ranges"][0] == "bytes=0-4194303" and len(s["ranges"]) >= 2 and all(s["ranges"])
    assert s["asked"] < size  # (the padding in the middle was never fetched)
    assert s["bar"].startswith("Signed by Jane Tester")
    assert s["biggestUpload"] < 2000
    assert "/api/sign/verify" in s["posts"]
    # (pdf.js counts the signature's own field as a form field: no looking for a printed form then.)
    assert s["formHint"] == "has-fields" or (s["formHint"] == "none" and "/api/form/detect" in s["posts"])
    # What needs all of it still gets it: the bytes to save are the file's.
    assert s["fullLength"] == size
    # (b) Opened later, from the page that is already up: a printed form of 5 MB, its bar and the panel's suggestion.
    f = out["form"]
    assert f["held"] is False and f["tabs"] == 2
    assert f["bar"].startswith("This looks like a form to fill in")
    assert "Make it fillable" in f["suggestions"]
    assert out["back"] == {"name": "Signed contract.pdf", "pages": 3, "formBar": None}
    # (c) Thumbnails: none until the Pages panel is shown, then only those near what it shows.
    t = out["thumbs"]
    assert t["pages"] == 120
    assert t["before"] == 0
    assert 0 < t["shown"] < 30 and t["first"] == 1 and t["drawn"] > 0
    assert t["listHeight"] > 120 * 150  # the list is as long as all of them
    assert t["jumped"]["current"] == 100 and t["jumped"]["rows"] < 40 and t["jumped"]["has1"] is False
    assert t["keys"] == [101, 101]  # arrow down from the current one, then Enter: that page is shown
    assert t["deleted"] == {"pages": 119, "rows": True}
    assert t["hiddenAgain"] == 0  # another document, panel closed: none again
    # (d) Enter in the password box opens the document (a wrong password asks again).
    assert out["locked"] == {"wrong": "That password is incorrect.", "pages": 4}
    # (e) From the start screen: nothing of the reader's is asked for a file until one is opened.
    assert out["home"] == {"early": False, "pages": 2, "name": "Small.pdf"}
