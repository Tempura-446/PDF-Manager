"""Third-party notices and the licence, as the app shows them."""
import importlib.util

import pytest

from pdfmanager import notices


def test_notices_list_what_zpdf_includes_and_installs():
    text = notices.third_party_notices()
    for name in ("pypdf", "pikepdf", "reportlab", "pdfplumber", "pyHanko"):
        assert f"== {name} " in text, name
    assert "== pdf.js " in text and "Apache License" in text            # vendored in static/pdfjs
    assert "Tesseract" in text and "pdf.ttf" in text                    # the OCR text layer's font
    assert "Mozilla Public License" in text                             # pikepdf's own licence text
    assert "== pdf-manager " not in text                                # not ZPDF itself


def test_each_package_says_its_licence_in_one_line():
    text = notices.third_party_notices()
    block = text.split("== pypdf ", 1)[1].split("\n== ", 1)[0]
    assert "Licence:" in block and "BSD" in block


def test_only_the_parts_installed_with_it_are_listed():
    names = {notices.normal_name(d.metadata["Name"]) for d in notices.distributions(extras=())}
    assert "pypdf" in names and "pdf-manager" not in names
    if importlib.util.find_spec("webview"):
        assert "pywebview" not in names
        with_app = {notices.normal_name(d.metadata["Name"]) for d in notices.distributions(extras=("app",))}
        assert "pywebview" in with_app


def test_the_licence_is_zpdfs_own():
    text = notices.licence_text()
    assert "PolyForm Internal Use License 1.0.0" in text and "Additional permission" in text


@pytest.fixture
def served():
    import threading

    from pdfmanager import gui

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, token = url.split("/#token=")
    yield base, token
    srv.shutdown()
    srv.server_close()


def test_about_endpoints(served):
    import urllib.request

    base, token = served

    def get(path):
        req = urllib.request.Request(base + path, headers={"X-Pdfm-Token": token})
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, res.headers.get("Content-Type"), res.read().decode("utf-8")

    status, ctype, text = get("/api/licence")
    assert status == 200 and ctype.startswith("text/plain") and "PolyForm" in text
    status, ctype, text = get("/api/notices")
    assert status == 200 and ctype.startswith("text/plain") and "== pypdf " in text


def test_a_source_folder_lists_what_its_pyproject_asks_for():
    # The release is made from a source folder, whose installed metadata can
    # be older than its pyproject.toml (OCR's packages came in 0.14).
    if not importlib.util.find_spec("rapidocr"):
        pytest.skip("needs the OCR packages installed")
    if not importlib.util.find_spec("tomllib"):
        pytest.skip("Python 3.10 reads the installed metadata instead (releases are made on 3.11+)")
    text = notices.third_party_notices()
    assert "== rapidocr " in text and "== onnxruntime " in text
