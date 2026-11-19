# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Priority #7 Phase 1: i18n framework, RTL direction, language pack install/remove, and HTTP API."""
import hashlib
import json
import threading
import urllib.request
import zipfile
from pathlib import Path

import pytest

from pdfmanager import appdata, core, gui, i18n, update


def _make_pack_zip(path, code="fr", name="Français", strings=None, version="0.58.0"):
    manifest = {"code": code, "name": name, "version": version, "automatic": True}
    ui = strings if strings is not None else {
        "nav.open": "Ouvrir un PDF",
        "reader.save": "Enregistrer",
        "status.pages_count": "{count} pages",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("ui.json", json.dumps(ui, ensure_ascii=False))
    return path


def test_builtin_english_interpolation_and_rtl_detection():
    cat = i18n.load_catalog("en")
    assert cat["app.name"] == "ZPDF"
    assert cat["reader.save"] == "Save"
    assert i18n.t("status.pages_count", lang="en", count=12) == "12 pages"
    assert i18n.t("status.saved", lang="en", name="doc.pdf") == "Saved doc.pdf"
    assert i18n.language_dir("en") == "ltr"
    assert i18n.language_dir("zh-Hans") == "ltr"
    assert i18n.language_dir("ar") == "rtl"
    assert i18n.language_dir("he-IL") == "rtl"


def test_install_switch_fallback_and_remove_language_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(update, "data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)

    pack_zip = _make_pack_zip(tmp_path / "it.zip", code="it", name="Italiano", strings={
        "nav.open": "Apri un PDF",
        "reader.save": "Salva",
        "status.pages_count": "{count} pagine",
    })
    good_sha = hashlib.sha256(pack_zip.read_bytes()).hexdigest()

    # Wrong checksum is refused
    with pytest.raises(core.PdfManagerError, match="checksum"):
        i18n.install_pack_from_zip(pack_zip, expected_sha256="0" * 64)

    # Valid install succeeds
    info = i18n.install_pack_from_zip(pack_zip, expected_sha256=good_sha)
    assert info["code"] == "it" and info["dir"] == "ltr"
    assert len(i18n.list_installed_packs()) == 1

    # Switch language and verify fallback for keys missing in the older/partial pack
    assert i18n.set_language("it") == "it"
    assert i18n.get_language() == "it"
    assert i18n.t("nav.open") == "Apri un PDF"
    assert i18n.t("reader.print") == "Print…"  # falls back to English!

    # Removing the active pack resets language cleanly to English
    i18n.remove_pack("it")
    assert i18n.list_installed_packs() == []
    assert i18n.get_language() == "en"
    assert i18n.t("nav.open") == "Open a PDF"


def test_i18n_http_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(update, "data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)

    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token

        # Verify static/i18n.js is served
        with urllib.request.urlopen(base + "/static/i18n.js") as res:
            assert res.status == 200
            assert "export function applyTranslations" in res.read().decode("utf-8")

        # GET /api/i18n
        req = urllib.request.Request(base + "/api/i18n", headers={"X-Pdfm-Token": token})
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode("utf-8"))
        assert data["lang"] == "en" and data["dir"] == "ltr"
        assert data["strings"]["reader.save"] == "Save"

        # Install Arabic RTL pack via POST /api/i18n/pack/install
        ar_zip = _make_pack_zip(tmp_path / "ar.zip", code="ar", name="العربية", strings={"reader.save": "حفظ"})
        boundary = "----zpdfi18n"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="ar.zip"\r\n'
            f"Content-Type: application/zip\r\n\r\n".encode("utf-8")
            + ar_zip.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        req_inst = urllib.request.Request(
            base + "/api/i18n/pack/install",
            data=body,
            headers={"X-Pdfm-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req_inst) as res:
            installed = json.loads(res.read().decode("utf-8"))
        assert installed["installed"]["code"] == "ar" and installed["installed"]["dir"] == "rtl"

        # Switch to Arabic via POST /api/i18n/language
        req_lang = urllib.request.Request(
            base + "/api/i18n/language",
            data=json.dumps({"language": "ar"}).encode("utf-8"),
            headers={"X-Pdfm-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_lang) as res:
            switched = json.loads(res.read().decode("utf-8"))
        assert switched["lang"] == "ar" and switched["dir"] == "rtl"
        assert switched["strings"]["reader.save"] == "حفظ"
        assert switched["strings"]["reader.print"] == "Print…"
    finally:
        srv.shutdown()
        srv.server_close()
