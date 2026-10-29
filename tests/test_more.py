"""Tests for progress and cancelling, recent files, single instance, headers
and footers, extracting pictures and attachments, folder search, form
fields, text edits, Windows set-up and updates."""

import io
import json
import threading
from pathlib import Path
import time
import urllib.error
import urllib.request

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from pdfmanager import core, gui, progress
from pdfmanager.cli import main


def text_pdf(path, pages=("Hello world",)):
    c = canvas.Canvas(str(path), pagesize=A4)
    for text in pages:
        c.setFont("Helvetica", 14)
        c.drawString(72, 760, text)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A running server whose app data lives in tmp_path."""
    from pdfmanager import appdata, recipes

    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(recipes, "data_dir", lambda: tmp_path / "appdata")
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token, srv
    srv.shutdown()
    srv.server_close()


def call(base, token, path, data=None, headers=None, method=None):
    headers = {"X-Pdfm-Token": token, **(headers or {})}
    if isinstance(data, (dict, list)):
        data, headers["Content-Type"] = json.dumps(data).encode(), "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def multipart(fields, files):
    boundary = "pdfmtestboundary"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    for name, path in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{path.name}"\r\nContent-Type: application/pdf\r\n\r\n'.encode()
                     + path.read_bytes() + b"\r\n")
    return (b"".join(parts) + f"--{boundary}--\r\n".encode(),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"})


# -- progress and cancelling ----------------------------------------------------------

def test_progress_sections_and_pages():
    seen = []

    def work():
        for i in range(2):
            with progress.section(i, 2, f"file{i}"):
                for n in range(4):
                    progress.report(n, 4, "page")
                    seen.append((round(job.fraction, 3), job.message))
        return "ok"

    job = progress.Job()
    job.run(work)
    assert job.state == "done" and job.result == "ok" and job.fraction == 1.0
    assert seen[0] == (0.0, "file0 — page 1 of 4")
    assert seen[5] == (0.625, "file1 — page 2 of 4")
    progress.report(1, 2, "page")  # outside a job: nothing happens


def test_cancelled_job_stops_at_the_next_report():
    reached = []

    def work():
        for n in range(1000):
            progress.report(n, 1000)
            reached.append(n)
            time.sleep(0.002)

    job = progress.Job().start(work)
    time.sleep(0.1)
    job.cancel()
    time.sleep(0.1)
    assert job.state == "cancelled" and len(reached) < 1000


def test_failed_job_reports_the_error():
    job = progress.Job()
    job.expected = (core.PdfManagerError,)
    job.run(lambda: (_ for _ in ()).throw(core.PdfManagerError("no good")))
    assert job.snapshot()["error"] == "no good"
    job = progress.Job()
    job.run(lambda: 1 / 0)
    assert job.snapshot()["error"].startswith("ZeroDivisionError")


def test_tools_run_as_jobs(app, tmp_path):
    base, token, _ = app
    src = text_pdf(tmp_path / "a.pdf", ["One", "Two", "Three"])
    body, headers = multipart({"op": "reverse"}, [("files", src)])
    status, _, out = call(base, token, "/api/run", body, {**headers, "X-Pdfm-Async": "1"})
    assert status == 200
    job = json.loads(out)["job"]
    for _ in range(100):
        state = json.loads(call(base, token, f"/api/job/{job}")[2])
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "done" and state["fraction"] == 1.0
    status, headers, data = call(base, token, f"/api/job/{job}/result")
    assert status == 200 and headers["X-Pdfm-Filename"] == "a_reversed.pdf"
    assert data.startswith(b"%PDF")
    assert call(base, token, f"/api/job/{job}")[0] == 404  # collected
    assert call(base, "wrong", f"/api/job/{job}")[0] == 403


def test_failed_job_result_is_an_error(app, tmp_path):
    base, token, _ = app
    body, headers = multipart({"op": "extract", "pages": "9"}, [("files", text_pdf(tmp_path / "a.pdf"))])
    job = json.loads(call(base, token, "/api/run", body, {**headers, "X-Pdfm-Async": "1"})[2])["job"]
    for _ in range(100):
        if json.loads(call(base, token, f"/api/job/{job}")[2])["state"] != "running":
            break
        time.sleep(0.05)
    status, _, out = call(base, token, f"/api/job/{job}/result")
    assert status == 400 and "out of range" in json.loads(out)["error"]


# -- recent files -------------------------------------------------------------------------

def test_recent_files(app, tmp_path):
    base, token, srv = app
    a, b = text_pdf(tmp_path / "a.pdf"), text_pdf(tmp_path / "b.pdf")
    opened = srv.RequestHandlerClass.opened
    for path in (a, b, a):  # opening by id (desktop Open dialog, "Open with") records it
        assert call(base, token, f"/api/file/{opened.add(path)}")[0] == 200
    files = json.loads(call(base, token, "/api/recent")[2])["files"]
    assert [f["name"] for f in files] == ["a.pdf", "b.pdf"]  # newest first, no repeats
    # Each comes with an id the reader can open, and files that are gone are left out.
    assert call(base, token, f"/api/file/{files[1]['id']}")[2].startswith(b"%PDF")
    b.unlink()
    assert [f["name"] for f in json.loads(call(base, token, "/api/recent")[2])["files"]] == ["a.pdf"]
    call(base, token, "/api/recent/forget", {"path": files[0]["path"]})
    assert json.loads(call(base, token, "/api/recent")[2])["files"] == []
    assert call(base, "wrong", "/api/recent")[0] == 403


# -- one running copy ---------------------------------------------------------------------

def test_files_are_handed_to_the_running_app(app, tmp_path, monkeypatch):
    base, token, srv = app
    pdf = text_pdf(tmp_path / "a.pdf")
    raised = threading.Event()
    release = threading.Event()

    def bring_to_front():  # pywebview can take a while (it waits for the window's thread)
        raised.set()
        release.wait(10)

    srv.RequestHandlerClass.bring_to_front = bring_to_front
    gui._register_instance(srv, "desktop")
    # Its own window: the app opens the files itself and comes to the front,
    # answering straight away (not after the window has come up).
    started = time.monotonic()
    assert gui.forward_to_running_app([pdf]) is True
    assert time.monotonic() - started < 3
    assert raised.wait(5)
    release.set()
    events = json.loads(call(base, token, "/api/events?after=0")[2])
    ids = events["events"][0]["open"]
    assert len(ids) == 1
    assert call(base, token, f"/api/file/{ids[0]}")[2].startswith(b"%PDF")
    # Edge window: the new launch opens a window on the running app.
    launched = []
    monkeypatch.setattr(gui.subprocess, "Popen", lambda args, **kw: launched.append(args))
    gui._register_instance(srv, "app-window", browser="/opt/edge", profile=tmp_path / "window")
    assert gui.forward_to_running_app([pdf]) is True
    assert launched[0][0] == "/opt/edge" and launched[0][1].startswith("--app=http://127.0.0.1:")
    assert f"#token={token}&open=" in launched[0][1]
    assert launched[0][2] == f"--user-data-dir={tmp_path / 'window'}"
    # Browser tab: a new tab.
    opened = []
    monkeypatch.setattr(gui.webbrowser, "open", opened.append)
    gui._register_instance(srv, "browser")
    assert gui.forward_to_running_app([]) is True and opened[0].endswith(f"#token={token}")
    gui._unregister_instance(srv)
    assert gui.forward_to_running_app([pdf]) is False  # nothing registered


def test_handing_over_needs_the_secret(app):
    base, token, srv = app
    gui._register_instance(srv, "desktop")
    assert call(base, token, "/api/instance", {"secret": "guess", "paths": []})[0] == 403


def test_stale_instance_file_is_ignored(app, tmp_path):
    from pdfmanager import appdata

    target = appdata.data_dir() / "instance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"port": 1, "secret": "x", "pid": 1, "mode": "desktop"}))
    assert gui.forward_to_running_app([]) is False


def test_closing_one_of_two_windows_keeps_the_app():
    pages = {"a": time.monotonic(), "b": time.monotonic()}
    handler = type("H", (), {"last_seen": time.monotonic(), "closed_at": None, "pages": pages})

    def close_a_then_b():
        time.sleep(0.2)
        pages.pop("a")
        handler.closed_at = time.monotonic()
        time.sleep(0.6)  # b is still open: the app must keep running
        assert not done
        pages.pop("b")
        handler.closed_at = time.monotonic()

    done = []
    threading.Thread(target=close_a_then_b, daemon=True).start()
    t = time.monotonic()
    gui.wait_until_closed(handler, poll=0.05, grace=0.2)
    done.append(1)
    assert 1.0 <= time.monotonic() - t < 2


def test_page_ids_are_tracked(app):
    base, token, srv = app
    call(base, token, "/api/ping", b"", {"X-Pdfm-Page": "p1"})
    assert "p1" in srv.RequestHandlerClass.pages
    call(base, token, f"/api/bye?token={token}&page=p1", b"")
    assert "p1" not in srv.RequestHandlerClass.pages


# -- headers and footers ------------------------------------------------------------------

def test_headers_and_footers(tmp_path):
    from pypdf import PdfWriter

    src = text_pdf(tmp_path / "doc.pdf", ["One", "Two", "Three"])
    writer = PdfWriter(clone_from=str(src))
    writer.pages[1].rotate(90)
    writer.write(src)
    nxt = core.headers_footers(src, tmp_path / "out.pdf",
                               {"top_right": "{file}", "bottom_center": "Page {n} of {total}",
                                "bottom_right": "{bates}", "top_left": "{unknown} stays"},
                               bates_prefix="ABC", bates_start=41, pages="2-3")
    assert nxt == 43
    reader = PdfReader(tmp_path / "out.pdf")
    assert "Page" not in reader.pages[0].extract_text()  # not in the page list
    second = reader.pages[1].extract_text()
    assert "Page 1 of 3" in second and "ABC000041" in second and "doc" in second
    assert "{unknown} stays" in second
    assert "ABC000042" in reader.pages[2].extract_text()
    with pytest.raises(core.PdfManagerError, match="at least one"):
        core.headers_footers(src, tmp_path / "x.pdf", {"top_left": " "})


def test_bates_numbers_continue_across_files(tmp_path, capsys):
    a = text_pdf(tmp_path / "a.pdf", ["1", "2"])
    b = text_pdf(tmp_path / "b.pdf", ["3", "4", "5"])
    assert main(["headers", str(a), str(b), "-o", str(tmp_path / "out"), "--bottom-right",
                 "{bates}", "--bates", "X-", "--bates-digits", "4"]) == 0
    out = capsys.readouterr().out
    assert "X-0001–X-0002" in out and "X-0003–X-0005" in out
    assert "X-0005" in PdfReader(tmp_path / "out" / "b.pdf").pages[2].extract_text()
    # The app does the same for several files.
    work = tmp_path / "work"
    work.mkdir()
    name, data, _ = gui.run_operation("headers", [a, b], {"bottom_right": "{bates}",
                                                         "bates_prefix": "Q", "bates_start": "9",
                                                         "bates_digits": "3"}, work=work)
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        second = PdfReader(io.BytesIO(z.read("b_headers.pdf")))
    assert "Q011" in second.pages[0].extract_text()


# -- pictures and attachments -----------------------------------------------------------------

def media_pdf(tmp_path):
    from PIL import Image
    from pypdf import PdfWriter

    Image.new("RGB", (300, 200), (200, 30, 30)).save(tmp_path / "red.jpg", quality=90)
    Image.new("RGB", (120, 120), (30, 30, 200)).save(tmp_path / "blue.png")
    Image.new("RGB", (4, 4), (0, 0, 0)).save(tmp_path / "dot.png")
    c = canvas.Canvas(str(tmp_path / "pics.pdf"), pagesize=A4)
    c.drawImage(str(tmp_path / "red.jpg"), 72, 500, 300, 200)
    c.drawImage(str(tmp_path / "dot.png"), 72, 400, 4, 4)
    c.showPage()
    c.drawImage(str(tmp_path / "red.jpg"), 72, 500, 150, 100)  # the same picture again
    c.drawImage(str(tmp_path / "blue.png"), 72, 300, 120, 120)
    c.showPage()
    c.save()
    writer = PdfWriter(clone_from=str(tmp_path / "pics.pdf"))
    writer.add_attachment("notes.txt", b"attached notes")
    writer.write(tmp_path / "pics.pdf")
    return tmp_path / "pics.pdf"


def test_extract_pictures_and_attachments(tmp_path):
    found = core.extract_media(media_pdf(tmp_path), tmp_path / "out")
    from PIL import Image

    # Each picture once (the red one is on both pages), the tiny dot skipped,
    # JPEG kept as JPEG.
    sizes = sorted((p.name.split("_")[1], p.suffix, Image.open(p).size) for p in found["images"])
    assert sizes == [("page1", ".jpg", (300, 200)), ("page2", ".png", (120, 120))]
    assert [p.read_bytes() for p in found["attachments"]] == [b"attached notes"]
    assert found["attachments"][0].name == "notes.txt"


def test_cli_and_app_extract_media(tmp_path, capsys):
    src = media_pdf(tmp_path)
    assert main(["extract-media", str(src), "--no-attachments"]) == 0
    assert "2 picture(s), 0 attachment(s)" in capsys.readouterr().out
    assert len(list((tmp_path / "pics_media").glob("pics_page1_*.jpg"))) == 1
    work = tmp_path / "work"
    work.mkdir()
    notes = []
    name, data, ctype = gui.run_operation("media", [src], {}, work=work, notes=notes)
    assert name == "pics_media.zip" and ctype == "application/zip"
    assert notes == ["pics.pdf: 2 pictures, 1 attached file"]
    work2 = tmp_path / "work2"
    work2.mkdir()
    with pytest.raises(gui.UserError, match="nothing was found"):
        gui.run_operation("media", [text_pdf(tmp_path / "plain.pdf")], {}, work=work2)


# -- folder search ------------------------------------------------------------------------------

def test_search_a_folder(tmp_path, monkeypatch):
    from pdfmanager import search

    monkeypatch.setattr(search, "data_dir", lambda: tmp_path / "appdata")
    folder = tmp_path / "docs"
    (folder / "sub").mkdir(parents=True)
    text_pdf(folder / "a.pdf", ["The invoice total", "nothing here", "Invoice again, invoice"])
    text_pdf(folder / "sub" / "b.pdf", ["Old INVOICE"])
    core.encrypt(text_pdf(folder / "c.pdf", ["invoice"]), folder / "locked.pdf", "pw")
    found = search.search(folder, "invoice")
    by_file = {(Path(m["path"]).name, m["page"]): m for m in found["matches"]}
    assert set(by_file) == {("a.pdf", 1), ("a.pdf", 3), ("c.pdf", 1)}  # not in sub/ without -R
    assert by_file[("a.pdf", 3)]["count"] == 2 and by_file[("a.pdf", 1)]["match"] == "invoice"
    assert [s[1] for s in found["skipped"]] == ["password protected"]
    deep = search.search(folder, "old invoice", recursive=True)
    assert [m["page"] for m in deep["matches"]] == [1]
    assert search.search(folder, "INVOICE", case_sensitive=True)["files"] == 3
    assert len(search.search(folder, "INVOICE", case_sensitive=True, recursive=True)["matches"]) == 1
    # The text is cached: a second search doesn't read the files again.
    calls = []
    monkeypatch.setattr(search, "_extract", lambda p: calls.append(p) or [])
    search.search(folder, "invoice")
    assert calls == []
    text_pdf(folder / "a.pdf", ["changed"])  # a changed file is read again
    search.search(folder, "invoice")
    assert [p.name for p in calls] == ["a.pdf"]


def test_cli_search_and_app(tmp_path, monkeypatch, capsys, app):
    from pdfmanager import search

    monkeypatch.setattr(search, "data_dir", lambda: tmp_path / "appdata")
    text_pdf(tmp_path / "x.pdf", ["Find the needle here"])
    assert main(["search", str(tmp_path), "needle"]) == 0
    assert "page 1: Find the [needle] here" in capsys.readouterr().out
    assert main(["search", str(tmp_path), "haystack"]) == 1
    base, token, _ = app
    status, _, body = call(base, token, "/api/folder", {"op": "search", "options": {
        "directory": str(tmp_path), "query": "needle"}})
    match = json.loads(body)["matches"][0]
    assert status == 200 and match["page"] == 1
    assert call(base, token, f"/api/file/{match['id']}")[2].startswith(b"%PDF")


# -- form fields ----------------------------------------------------------------------------------

def test_add_form_fields(tmp_path):
    from pypdf import PdfWriter
    from pdfmanager import forms

    src = text_pdf(tmp_path / "blank.pdf", ["Name:", "Sideways"])
    w = PdfWriter(clone_from=src)
    w.pages[1].rotate(90)
    w.write(src)
    out = tmp_path / "form.pdf"
    names = forms.add_fields(src, out, [
        {"page": 1, "rect": [120, 756, 400, 776], "type": "text", "name": "Name"},
        {"page": 1, "rect": [140, 716, 152, 728], "type": "checkbox"},
        {"page": 1, "rect": [72, 500, 400, 600], "type": "multiline", "required": True},
        {"page": 1, "rect": [272, 450, 72, 400], "type": "signature"},  # corners in any order
        {"page": 2, "rect": [72, 700, 300, 720], "type": "text", "name": "Na.me"},
    ])
    assert names == ["Name", "Checkbox 1", "Text 1", "Signature 1", "Na me"]
    reader = PdfReader(out)
    fields = reader.get_fields()
    assert {k: v["/FT"] for k, v in fields.items()} == {
        "Name": "/Tx", "Checkbox 1": "/Btn", "Text 1": "/Tx", "Signature 1": "/Sig", "Na me": "/Tx"}
    assert fields["Text 1"]["/Ff"] == forms.MULTILINE | forms.REQUIRED
    assert reader.trailer["/Root"]["/AcroForm"]["/SigFlags"] == 1
    box = [a.get_object() for a in reader.pages[0]["/Annots"]][1]
    assert set(box["/AP"]["/N"]) == {"/Yes", "/Off"} and box["/AS"] == "/Off"
    sideways = reader.pages[1]["/Annots"][0].get_object()
    assert sideways["/MK"]["/R"] == 90 and list(sideways["/AP"]["/N"]["/BBox"]) == [0, 0, 20, 228]
    assert reader.pages[0]["/Tabs"] == "/R"
    found = forms.existing_fields(out)
    assert [f["name"] for f in found[1]] == ["Name", "Checkbox 1", "Text 1", "Signature 1"]
    assert found[1][3] == {"rect": [72.0, 400.0, 272.0, 450.0], "name": "Signature 1",
                           "type": "signature"}
    # adding more keeps the names unique
    again = forms.add_fields(out, tmp_path / "more.pdf", [
        {"page": 1, "rect": [10, 10, 100, 30], "type": "text"},
        {"page": 1, "rect": [10, 40, 100, 60], "type": "text", "name": "Name"}])
    assert again == ["Name 2", "Text 2"] or again == ["Text 2", "Name 2"]
    assert len(PdfReader(tmp_path / "more.pdf").get_fields()) == 7
    # filling in works
    filled = PdfWriter(clone_from=out)
    filled.update_page_form_field_values(filled.pages[0], {"Name": "Ada", "Checkbox 1": "/Yes"},
                                         auto_regenerate=False)
    filled.write(tmp_path / "filled.pdf")
    values = PdfReader(tmp_path / "filled.pdf").get_fields()
    assert values["Name"]["/V"] == "Ada" and values["Checkbox 1"]["/V"] == "/Yes"


def test_form_field_mistakes(tmp_path):
    from pdfmanager import forms

    src = text_pdf(tmp_path / "blank.pdf")
    with pytest.raises(core.PdfManagerError, match="unknown field type"):
        forms.add_fields(src, tmp_path / "o.pdf", [{"page": 1, "rect": [0, 0, 50, 50], "type": "radio"}])
    with pytest.raises(core.PdfManagerError, match="page 3 doesn't exist"):
        forms.add_fields(src, tmp_path / "o.pdf", [{"page": 3, "rect": [0, 0, 50, 50]}])
    with pytest.raises(core.PdfManagerError, match="too small"):
        forms.add_fields(src, tmp_path / "o.pdf", [{"page": 1, "rect": [0, 0, 2, 50]}])
    with pytest.raises(core.PdfManagerError, match="no fields"):
        forms.add_fields(src, tmp_path / "o.pdf", [])
    with pytest.raises(core.PdfManagerError, match="invalid field"):
        forms.parse_field("text:1:1,2,3")
    assert forms.parse_field("checkbox:2:1,2,3,4:I: agree") == {
        "type": "checkbox", "page": 2, "rect": [1, 2, 3, 4], "name": "I: agree"}


def test_cli_and_app_form_fields(tmp_path, capsys, app):
    src = text_pdf(tmp_path / "blank.pdf")
    assert main(["form", str(src), "-f", "text:1:72,700,300,720:Name", "-f",
                 "checkbox:1:72,650,84,662", "--no-border"]) == 0
    assert "2 fields: Name, Checkbox 1" in capsys.readouterr().out
    out = tmp_path / "blank_form.pdf"
    assert main(["form", str(out)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "text:1:72,700,300,720:Name", "checkbox:1:72,650,84,662:Checkbox 1"]
    assert "/BS" not in PdfReader(out).pages[0]["/Annots"][0].get_object()

    work = tmp_path / "work"
    work.mkdir()
    notes = []
    fields = {"1": [{"rect": [72, 600, 300, 620], "type": "text", "name": "Email"},
                    {"rect": [72, 500, 300, 560], "type": "multiline", "name": "Notes"}]}
    name, data, _ = gui.run_operation("form", [src], {"fields": json.dumps(fields), "border": "true",
                                                      "required": "true", "font_size": "12"},
                                      work=work, notes=notes)
    assert name == "blank_form.pdf" and notes == ["blank.pdf: added 2 fields (Email, Notes)"]
    email = PdfReader(io.BytesIO(data)).pages[0]["/Annots"][0].get_object()
    assert email["/T"] == "Email" and email["/DA"] == "/Helv 12 Tf 0 g" and email["/Ff"] == 2
    with pytest.raises(gui.UserError, match="draw the fields"):
        gui.run_operation("form", [src], {"fields": "{}"}, work=tmp_path)

    base, token, _ = app
    body, headers = multipart({"password": ""}, [("file", out)])
    status, _, res = call(base, token, "/api/form/fields", body, headers)
    assert status == 200
    assert [f["name"] for f in json.loads(res)["fields"]["1"]] == ["Name", "Checkbox 1"]


# -- changing words on a page ---------------------------------------------------------------------

def edit_pdf(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillColorRGB(0.85, 0.92, 1)
    c.rect(0, 600, 595, 242, fill=1, stroke=0)
    c.setFillColorRGB(0.8, 0, 0)
    c.setFont("Times-Bold", 18)
    c.drawString(72, 760, "Invoice for March")
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Total due: 1,200.00 EUR")
    c.save()
    return path


def test_edit_text(tmp_path):
    import pypdfium2 as pdfium
    from pdfmanager import textedit

    src = edit_pdf(tmp_path / "invoice.pdf")
    out = tmp_path / "edited.pdf"
    assert textedit.edit_text(src, out, [
        {"page": 1, "rect": [72, 755, 230, 775], "text": "Invoice for April"},
        {"page": 1, "rect": [72, 697, 200, 711], "text": ""},  # just cover it
        {"page": 1, "rect": [300, 300, 400, 320], "text": "New words"},  # nothing there before
    ]) == 3
    doc = pdfium.PdfDocument(str(out))
    page = doc[0]
    text = page.get_textpage().get_text_range()
    assert "Invoice for April" in text and "New words" in text
    assert "Invoice for March" in text  # still underneath, as the tool says
    image = page.render(scale=1).to_pil().convert("RGB")
    # the new title is red like the old one
    reds = [image.getpixel((x, 842 - 763)) for x in range(72, 230)]
    assert any(r > 150 and g < 60 and b < 60 for r, g, b in reds)
    # the covered total is gone from view: the line is all background colour
    row = {image.getpixel((x, 842 - 703)) for x in range(72, 200)}
    assert row == {image.getpixel((60, 842 - 703))}
    doc.close()
    # the new title uses the same kind of font, size and colour
    from pypdf import PdfReader as Reader
    fonts = Reader(out).pages[0]["/Resources"]["/Font"]
    assert any(f.get_object()["/BaseFont"] == "/Times-Bold" for f in fonts.values())
    with pytest.raises(core.PdfManagerError, match="page 2 doesn't exist"):
        textedit.edit_text(src, out, [{"page": 2, "rect": [0, 0, 1, 1], "text": "x"}])
    with pytest.raises(core.PdfManagerError, match="no changes"):
        textedit.edit_text(src, out, [])


def test_edit_text_endpoint_keeps_the_password(app, tmp_path):
    base, token, _ = app
    src = edit_pdf(tmp_path / "invoice.pdf")
    core.encrypt(src, tmp_path / "locked.pdf", "secret")
    edits = json.dumps([{"page": 1, "rect": [72, 755, 230, 775], "text": "Invoice for May"}])
    body, headers = multipart({"password": "secret", "edits": edits}, [("file", tmp_path / "locked.pdf")])
    status, res_headers, data = call(base, token, "/api/text/edit", body, headers)
    assert status == 200 and res_headers["Content-Type"] == "application/pdf"
    reader = PdfReader(io.BytesIO(data))
    assert reader.is_encrypted and reader.decrypt("secret")
    assert "Invoice for May" in reader.pages[0].extract_text()
    body, headers = multipart({"password": "wrong", "edits": edits}, [("file", tmp_path / "locked.pdf")])
    assert call(base, token, "/api/text/edit", body, headers)[0] == 400


# -- Windows set-up -------------------------------------------------------------------------------

class FakeRegistry:
    """Enough of winreg for winsetup, keeping keys in a dict."""
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = KEY_READ = KEY_WRITE = 0
    REG_SZ, REG_DWORD = 1, 4

    def __init__(self):
        self.keys = {}

    class _Key:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        parts = path.lower().split("\\")
        for n in range(1, len(parts) + 1):  # with the keys above it, like Windows
            self.keys.setdefault("\\".join(parts[:n]), {})
        return self._Key(self, path.lower())

    def OpenKey(self, root, path, reserved=0, access=0):
        if path.lower() not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(self, path.lower())

    def SetValueEx(self, key, name, reserved, kind, value):
        self.keys[key.path][name] = value

    def DeleteValue(self, key, name):
        self.keys[key.path].pop(name, None)

    def EnumKey(self, key, index):
        children = sorted({k[len(key.path) + 1:].split("\\")[0] for k in self.keys
                           if k.startswith(key.path + "\\")})
        if index >= len(children):
            raise OSError("no more")
        return children[index]

    def DeleteKey(self, root, path):
        if any(k.startswith(path.lower() + "\\") for k in self.keys):
            raise PermissionError("key has subkeys")  # as winreg does
        self.keys.pop(path.lower())


def test_windows_setup_registers_and_removes(tmp_path, monkeypatch):
    import sys
    from pdfmanager import winsetup

    reg = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(winsetup.sys, "platform", "win32")
    folders = {winsetup.CSIDL_PROGRAMS: tmp_path / "Start", winsetup.CSIDL_DESKTOPDIRECTORY: tmp_path / "Desktop"}
    monkeypatch.setattr(winsetup, "_folder", folders.get)
    monkeypatch.setattr(winsetup, "_notify_shell", lambda: None)
    made = []

    def fake_shortcut(path, target, arguments="", **kw):
        made.append((Path(path), Path(target).name, arguments))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("lnk")

    monkeypatch.setattr(winsetup, "make_shortcut", fake_shortcut)
    app_dir = tmp_path / "app"
    done = winsetup.install(app_dir)
    assert len(done) == 4
    assert made[0][0] == tmp_path / "Start" / "PDF Manager.lnk"
    assert made[1][0] == tmp_path / "Desktop" / "PDF Manager.lnk"
    assert made[0][2] == "-m pdfmanager gui"
    assert (app_dir / "PDF Manager.ico").stat().st_size > 1000
    assert str(app_dir) in (app_dir / "uninstall.ps1").read_text(encoding="utf-8-sig")
    keys = reg.keys
    command = keys[r"software\classes\pdfmanager.document\shell\open\command"][""]
    assert command.endswith('-m pdfmanager gui "%1"')
    assert keys[r"software\classes\.pdf\openwithprogids"] == {"PDFManager.Document": ""}
    assert keys[r"software\registeredapplications"]["PDF Manager"] == r"Software\PDFManager\Capabilities"
    uninstall = keys[r"software\microsoft\windows\currentversion\uninstall\pdfmanager"]
    assert uninstall["DisplayName"] == "PDF Manager" and "uninstall.ps1" in uninstall["UninstallString"]
    winsetup.remove()
    assert not (tmp_path / "Start" / "PDF Manager.lnk").exists()
    assert not (tmp_path / "Desktop" / "PDF Manager.lnk").exists()
    assert not any(k.startswith(r"software\classes\pdfmanager") or k.startswith(r"software\pdfmanager")
                   or k.endswith(r"uninstall\pdfmanager") for k in keys)
    assert keys[r"software\classes\.pdf\openwithprogids"] == {}
    assert "PDF Manager" not in keys[r"software\registeredapplications"]
    # without a desktop shortcut or Open with
    made.clear()
    reg.keys.clear()
    winsetup.install(app_dir, desktop=False, open_with=False)
    assert [m[0].parent.name for m in made] == ["Start"]
    assert not any("pdfmanager.document" in k for k in reg.keys)


def test_windows_setup_needs_windows(monkeypatch, capsys):
    import sys
    if sys.platform == "win32":
        pytest.skip("this is Windows")
    assert main(["setup-windows"]) == 1
    assert "only works on Windows" in capsys.readouterr().err


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows only")
def test_windows_shortcuts_and_registry_for_real(tmp_path, monkeypatch):
    import winreg
    from pdfmanager import winsetup

    folders = {winsetup.CSIDL_PROGRAMS: tmp_path / "Start", winsetup.CSIDL_DESKTOPDIRECTORY: tmp_path / "Desktop"}
    monkeypatch.setattr(winsetup, "_folder", folders.get)
    winsetup.install(tmp_path / "app")
    try:
        target, args = winsetup.read_shortcut(tmp_path / "Start" / "PDF Manager.lnk")
        assert Path(target).name.lower() in ("pythonw.exe", "python.exe") and args == "-m pdfmanager gui"
        assert (tmp_path / "Desktop" / "PDF Manager.lnk").exists()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, winsetup.UNINSTALL_KEY) as key:
            assert winreg.QueryValueEx(key, "DisplayName")[0] == "PDF Manager"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"{winsetup.CLASSES}\{winsetup.PROG_ID}\shell\open\command") as key:
            assert winreg.QueryValueEx(key, "")[0].endswith('-m pdfmanager gui "%1"')
    finally:
        winsetup.remove()
    assert not (tmp_path / "Start" / "PDF Manager.lnk").exists()
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, winsetup.UNINSTALL_KEY)


# -- updates --------------------------------------------------------------------------------------

class FakeGitHub:
    """Answers update.py's requests like GitHub's API."""

    def __init__(self, version="9.0.0", status=200, zipball=b""):
        self.version, self.status, self.zipball, self.requests = version, status, zipball, []

    real = staticmethod(urllib.request.urlopen)

    def __call__(self, req, timeout=None):
        import urllib.error

        if not getattr(req, "full_url", str(req)).startswith("https://api.github.com"):
            return self.real(req) if timeout is None else self.real(req, timeout=timeout)
        self.requests.append(req)
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, "Not Found", {}, None)
        url = req.full_url
        if url.endswith("/repos/Tempura-446/PDF-Manager"):
            body = json.dumps({"default_branch": "main"}).encode()
        elif "/contents/pdfmanager/__init__.py?ref=main" in url:
            body = f'"""PDF Manager."""\n\n__version__ = "{self.version}"\n'.encode()
        elif url.endswith("/zipball/main"):
            body = self.zipball
        else:
            raise AssertionError(url)
        return io.BytesIO(body)


@pytest.fixture
def updates(tmp_path, monkeypatch):
    from pdfmanager import appdata, update

    monkeypatch.setattr(update, "data_dir", lambda: tmp_path / "appdata")
    github = FakeGitHub()
    monkeypatch.setattr(update.urllib.request, "urlopen", github)
    return update, github


def test_update_check(updates):
    from pdfmanager import __version__

    update, github = updates
    info = update.check()
    assert info["current"] == __version__ and info["latest"] == "9.0.0" and info["available"]
    assert info["error"] is None and info["how"] == "source"  # this test runs from a checkout
    asked = len(github.requests)
    assert update.check()["available"] and len(github.requests) == asked  # remembered for a while
    github.version = __version__
    assert not update.check(force=True)["available"]
    # a private repository without a token
    github.status = 404
    info = update.check(force=True)
    assert not info["available"] and "add a GitHub token in Settings" in info["error"]
    update.save_settings(github_token="ghp_secret")
    assert "the token can't read the repository" in update.check(force=True)["error"]
    req = github.requests[-1]
    assert req.unredirected_hdrs["Authorization"] == "Bearer ghp_secret"
    assert "Authorization" not in req.headers  # not sent on to other servers after a redirect
    # turned off: no requests
    update.save_settings(check_updates=False)
    (update.data_dir() / "update.json").unlink()
    asked = len(github.requests)
    assert update.check()["latest"] is None and len(github.requests) == asked
    assert update.parse_version("0.10.1") > update.parse_version("0.9.9")


def test_update_install(updates, tmp_path, monkeypatch):
    import zipfile

    update, github = updates
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Tempura-446-PDF-Manager-abc123/pyproject.toml", "[project]\n")
        zf.writestr("Tempura-446-PDF-Manager-abc123/pdfmanager/__init__.py", '__version__ = "9.0.0"\n')
    github.zipball = buf.getvalue()
    with pytest.raises(core.PdfManagerError, match="git pull"):
        update.install()  # running from a checkout
    monkeypatch.setattr(update, "how_installed", lambda: "pip")
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        assert (Path(cmd[-1].replace("[app]", "")) / "pyproject.toml").exists()
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert update.install(python="py") == "9.0.0"
    assert ran[0][:5] == ["py", "-m", "pip", "install", "--upgrade"]
    assert ran[0][-1].split("Tempura-446-PDF-Manager-abc123")[1] in ("", "[app]")
    monkeypatch.setattr(update.subprocess, "run", lambda cmd, **kw: type(
        "Failed", (), {"returncode": 1, "stdout": "", "stderr": "ERROR: no space left"})())
    with pytest.raises(core.PdfManagerError, match="no space left"):
        update.install()
    # a download with paths leading outside its folder is refused
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(core.PdfManagerError, match="unsafe"):
        update._extract(bad.getvalue(), tmp_path / "x")


def test_settings_and_update_endpoints(app, updates):
    base, token, srv = app
    update, github = updates
    status, _, data = call(base, token, "/api/settings")
    assert status == 200 and json.loads(data) == {"repo": "Tempura-446/PDF-Manager", "token_set": False,
                                                 "check": True, "how": "source"}
    status, _, data = call(base, token, "/api/settings", {"token": "ghp_x", "check": False})
    assert json.loads(data)["token_set"] and "ghp_x" not in data.decode()
    assert update.settings()["github_token"] == "ghp_x" and update.settings()["check_updates"] is False
    assert call(base, token, "/api/settings", {"repo": "not a repo"})[0] == 400
    call(base, token, "/api/settings", {"clear_token": True, "check": True})
    assert "github_token" not in update.settings() and "check_updates" not in update.settings()
    status, _, data = call(base, token, "/api/update/check", {})
    info = json.loads(data)
    assert info["available"] and info["latest"] == "9.0.0" and info["can_restart"] is False
    status, _, data = call(base, token, "/api/update/restart", {})
    assert status == 400 and "open it again" in json.loads(data)["error"]


def test_restart_ends_the_wait():
    class H:
        pages, closed_at, last_seen, quit_requested = {}, None, time.monotonic(), False

    threading.Timer(0.3, lambda: setattr(H, "quit_requested", True)).start()
    started = time.monotonic()
    gui.wait_until_closed(H, poll=0.05)
    assert time.monotonic() - started < 2
