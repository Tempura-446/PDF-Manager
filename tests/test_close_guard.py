"""0.29.0, from the Windows check (gui.py): closing the desktop window with its
X asks first when anything is unsaved. WebView2 fires no beforeunload then,
so the page keeps the server told what's unsaved (/api/unsaved) and
pywebview's closing event asks from there (may_close), with a native
Yes/No box on Windows. Closing on purpose (to start again after an update)
never asks. Tested without a real window."""
import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

from pdfmanager import gui


class FakeHandler:
    quit_requested = False

    def __init__(self, *names):
        self.unsaved = gui.UnsavedWork()
        if names:
            self.unsaved.said("page-1", list(names))


def test_the_question_names_a_few_files_and_counts_many():
    assert gui.close_question([]) is None
    assert gui.close_question(["Report.pdf"]) == "Close ZPDF? Your changes to “Report.pdf” haven't been saved."
    assert gui.close_question(["a.pdf", "b.pdf"]) == "Close ZPDF? Your changes to 2 files haven't been saved: a.pdf, b.pdf."
    assert gui.close_question([f"{n}.pdf" for n in range(5)]) == "Close ZPDF? Your changes to 5 files haven't been saved."


def test_nothing_unsaved_closes_without_asking():
    asked = []
    assert gui.may_close(FakeHandler(), asked.append) is True
    assert asked == []


@pytest.mark.parametrize("answer, closes", [(True, True), (False, False)])
def test_unsaved_work_asks_and_the_answer_decides(answer, closes):
    asked = []
    assert gui.may_close(FakeHandler("Report.pdf", "Merged.pdf"), lambda text: asked.append(text) or answer) is closes
    assert asked == ["Close ZPDF? Your changes to 2 files haven't been saved: Report.pdf, Merged.pdf."]


def test_closing_on_purpose_never_asks():
    """The update's restart (and anything else closing the window itself) asked already."""
    handler, asked = FakeHandler("Report.pdf"), []
    destroyed = []
    window = type("Window", (), {"destroy": lambda self: destroyed.append(1)})()
    gui._close_on_purpose(handler, window)
    assert destroyed == [1] and handler.quit_requested
    assert gui.may_close(handler, asked.append) is True and asked == []


def test_a_question_that_fails_lets_the_window_close():
    def broken(text):
        raise OSError("no message box")

    assert gui.may_close(FakeHandler("Report.pdf"), broken) is True


def test_unsaved_work_follows_each_page():
    work = gui.UnsavedWork()
    work.said("a", ["Report.pdf"])
    work.said("b", ["Scan.pdf"])
    assert sorted(work.names()) == ["Report.pdf", "Scan.pdf"]
    work.said("a", [])
    assert work.names() == ["Scan.pdf"]
    work.gone("b")
    assert work.names() == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows asks with MessageBoxW")
def test_elsewhere_the_main_thread_closes_rather_than_waiting_for_itself():
    asked = []
    window = type("Window", (), {"create_confirmation_dialog": lambda self, title, text: asked.append(text) or False})()
    assert gui._ask_to_close(window, "Close ZPDF?") is True and asked == []  # (pywebview's dialog would wait for this thread)
    result = []
    worker = threading.Thread(target=lambda: result.append(gui._ask_to_close(window, "Close ZPDF? ...")))
    worker.start()
    worker.join()
    assert result == [False] and asked == ["Close ZPDF? ..."]


# -- the endpoint -------------------------------------------------------------------

@pytest.fixture
def server():
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield srv, base, token
    srv.shutdown()
    srv.server_close()


def post(base, path, token, body, headers=None):
    request = urllib.request.Request(base + path, data=body, method="POST",
                                     headers={"X-Pdfm-Token": token, "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_the_page_tells_the_server_what_is_unsaved(server):
    srv, base, token = server
    work = srv.RequestHandlerClass.unsaved
    assert post(base, "/api/unsaved", token, json.dumps({"page": "p1", "names": ["Report.pdf"]}).encode()) == (200, {"ok": True})
    assert work.names() == ["Report.pdf"]
    assert gui.close_question(work.names()).endswith("“Report.pdf” haven't been saved.")
    # Saved: nothing to ask about.
    assert post(base, "/api/unsaved", token, json.dumps({"page": "p1", "names": []}).encode())[0] == 200
    assert work.names() == []
    # The page id from the header when the body has none.
    assert post(base, "/api/unsaved", token, b'{"names": ["A.pdf"]}', {"X-Pdfm-Page": "p2"})[0] == 200
    assert work.names() == ["A.pdf"]
    # The page closed (or reloaded): its tabs go with it.
    with urllib.request.urlopen(urllib.request.Request(f"{base}/api/bye?token={token}&page=p2", data=b"", method="POST"),
                                timeout=10) as res:
        assert res.status == 200
    assert work.names() == []


def test_the_endpoint_needs_the_token_and_a_list_of_names(server):
    srv, base, token = server
    assert post(base, "/api/unsaved", "wrong", b'{"page": "p", "names": ["x.pdf"]}')[0] == 403
    for bad in (b"not json", b'{"page": "p"}', b'{"names": "x.pdf"}', b'{"names": [1, 2]}'):
        assert post(base, "/api/unsaved", token, bad)[0] == 400, bad
    assert srv.RequestHandlerClass.unsaved.names() == []


# -- the window ----------------------------------------------------------------------

def test_the_desktop_window_asks_through_its_closing_event(tmp_path, monkeypatch):
    """run_desktop hooks may_close to pywebview's closing event, and the
    update's restart closes without it asking."""
    seen = {}

    class Event(list):
        def __iadd__(self, fn):
            self.append(fn)
            return self

    class Window:
        def __init__(self):
            self.events = type("Events", (), {})()
            self.events.closing = Event()
            self.events.before_show = Event()
            self.destroyed = False

        def destroy(self):
            self.destroyed = True

    def start(**kwargs):
        window, handler = seen["window"], seen["handler"]
        [closing] = window.events.closing
        handler.unsaved.said("p", ["Report.pdf"])
        seen["asked"] = []
        monkeypatch.setattr(gui, "_ask_to_close", lambda w, text: seen["asked"].append(text) or False)
        seen["no"] = closing()
        handler.quit_app()  # (the update's restart)
        seen["restart"] = closing()
        seen["destroyed"] = window.destroyed

    fake = type(sys)("webview")

    def create_window(*args, **kwargs):
        seen["window"] = Window()
        return seen["window"]

    fake.create_window = create_window
    fake.start = start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(gui, "desktop_available", lambda: True)
    monkeypatch.setattr(gui, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui, "_pywebview_version", lambda: "6.0")
    real = gui.make_server

    def make_server(*args, **kwargs):
        server, url = real(*args, **kwargs)
        seen["handler"] = server.RequestHandlerClass
        return server, url

    monkeypatch.setattr(gui, "make_server", make_server)
    monkeypatch.setattr(gui, "_register_instance", lambda server, mode, **extra: None)
    monkeypatch.setattr(gui, "_unregister_instance", lambda server: None)
    assert gui.run_desktop() is True
    assert seen["no"] is False and seen["asked"] == ["Close ZPDF? Your changes to “Report.pdf” haven't been saved."]
    assert seen["restart"] is True and seen["destroyed"] and len(seen["asked"]) == 1
