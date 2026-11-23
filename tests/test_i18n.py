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


def _make_argos_zip(path, from_code="en", to_code="zh", from_name="English", to_name="Chinese"):
    root = f"translate-{from_code}_{to_code}-1_9"
    meta = {
        "from_code": from_code,
        "to_code": to_code,
        "from_name": from_name,
        "to_name": to_name,
        "package_version": "1.9",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/metadata.json", json.dumps(meta))
        zf.writestr(f"{root}/sentencepiece.model", b"FAKE-SPM-DATA")
        zf.writestr(f"{root}/model/model.bin", b"FAKE-CT2-WEIGHTS")
        zf.writestr(f"{root}/model/config.json", '{"model": "transformer"}')
        zf.writestr(f"{root}/stanza/ignored.pt", b"SHOULD-BE-SKIPPED")
    return path


def test_install_argos_model_zip_maps_zh_and_skips_stanza(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(update, "data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)

    argos_zip = _make_argos_zip(tmp_path / "en_zh.argosmodel", "en", "zh", "English", "Chinese")
    # Also verify install_pack_from_zip auto-detects .argosmodel archives
    res = i18n.install_pack_from_zip(argos_zip)
    assert res["code"] == "zh-Hans"
    assert res["pair"] == "en-zh-Hans"

    model_dir = i18n.packs_dir() / "zh-Hans" / "models" / "en-zh-Hans"
    assert (model_dir / "model.bin").is_file()
    assert (model_dir / "sentencepiece.model").is_file()
    assert not any(model_dir.rglob("ignored.pt"))

    packs = i18n.list_installed_packs()
    assert len(packs) == 1
    assert packs[0]["has_translate"] is True
    assert packs[0]["pairs"] == ["en-zh-Hans"]


def test_hybrid_catalog_and_download_zpdf_plus_argos_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(update, "data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)

    # Build a fake ZPDF curated pack for Spanish + SHA-256
    es_zip = _make_pack_zip(tmp_path / "ZPDF-lang-es-0.60.2.zip", code="es", name="Español", strings={"reader.save": "Guardar"})
    es_bytes = es_zip.read_bytes()
    es_sha = hashlib.sha256(es_bytes).hexdigest()

    # Build fake Argos models for Japanese (en->ja and ja->en)
    en_ja = _make_argos_zip(tmp_path / "en_ja.argosmodel", "en", "ja", "English", "Japanese").read_bytes()
    ja_en = _make_argos_zip(tmp_path / "ja_en.argosmodel", "ja", "en", "Japanese", "English").read_bytes()

    fake_github_release = {
        "assets": [
            {
                "name": "ZPDF-lang-es-0.60.2.zip",
                "browser_download_url": "https://github.com/Tempura-446/ZPDF-releases/releases/download/langpacks-v1/ZPDF-lang-es-0.60.2.zip",
                "size": len(es_bytes),
            },
            {
                "name": "ZPDF-lang-es-0.60.2.zip.sha256",
                "browser_download_url": "https://github.com/Tempura-446/ZPDF-releases/releases/download/langpacks-v1/ZPDF-lang-es-0.60.2.zip.sha256",
                "size": 90,
            },
        ]
    }
    fake_argos_index = [
        {"from_code": "en", "to_code": "es", "from_name": "English", "to_name": "Spanish", "package_version": "1.9",
         "links": ["https://argos.example/en_es.argosmodel"]},
        {"from_code": "en", "to_code": "ja", "from_name": "English", "to_name": "Japanese", "package_version": "1.9",
         "links": ["https://argos.example/en_ja.argosmodel"]},
        {"from_code": "ja", "to_code": "en", "from_name": "Japanese", "to_name": "English", "package_version": "1.9",
         "links": ["https://argos.example/ja_en.argosmodel"]},
    ]

    responses = {
        f"{update.API}/repos/{update.RELEASES}/releases/tags/{i18n.LANGPACKS_RELEASE_TAG}": json.dumps(fake_github_release).encode(),
        i18n.ARGOS_INDEX_URL: json.dumps(fake_argos_index).encode(),
        "https://github.com/Tempura-446/ZPDF-releases/releases/download/langpacks-v1/ZPDF-lang-es-0.60.2.zip": es_bytes,
        "https://github.com/Tempura-446/ZPDF-releases/releases/download/langpacks-v1/ZPDF-lang-es-0.60.2.zip.sha256": f"{es_sha}  ZPDF-lang-es-0.60.2.zip\n".encode(),
        "https://argos.example/en_ja.argosmodel": en_ja,
        "https://argos.example/ja_en.argosmodel": ja_en,
    }

    def fake_get(url, *, headers=None, timeout=30):
        if url not in responses:
            raise core.PdfManagerError(f"unexpected url: {url}")
        return responses[url]

    monkeypatch.setattr(i18n, "_http_get_bytes", fake_get)

    # 1. Hybrid catalog prefers ZPDF for 'es' and falls back to Argos for 'ja'
    cat = i18n.list_catalog_packs(fetch=True)
    by_code = {p["code"]: p for p in cat["packs"]}
    assert by_code["es"]["source"] == "zpdf"
    assert by_code["ja"]["source"] == "argos"
    assert sorted(by_code["ja"]["available_pairs"]) == ["en-ja", "ja-en"]

    # 2. Download 'es' -> uses curated ZPDF pack with SHA-256 verification
    res_es = i18n.download_and_install_pack("es")
    assert res_es["source"] == "zpdf"
    assert i18n.load_catalog("es")["reader.save"] == "Guardar"

    # 3. Download 'ja' -> falls back to Argos and installs both en-ja and ja-en
    res_ja = i18n.download_and_install_pack("ja")
    assert res_ja["source"] == "argos"
    assert sorted(res_ja["installed_pairs"]) == ["en-ja", "ja-en"]

    # 4. HTTP endpoints (/api/i18n/catalog and /api/i18n/pack/download)
    srv, url = gui.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = url.split("#", 1)[0].rstrip("/")
        token = srv.RequestHandlerClass.token
        req = urllib.request.Request(base + "/api/i18n/catalog", headers={"X-Pdfm-Token": token})
        with urllib.request.urlopen(req) as r:
            http_cat = json.loads(r.read().decode("utf-8"))
        http_by_code = {p["code"]: p for p in http_cat["packs"]}
        assert http_by_code["ja"]["has_translate"] is True
        assert sorted(http_by_code["ja"]["installed_pairs"]) == ["en-ja", "ja-en"]

        req_dl = urllib.request.Request(
            base + "/api/i18n/pack/download",
            data=json.dumps({"code": "es"}).encode("utf-8"),
            headers={"X-Pdfm-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_dl) as r:
            http_dl = json.loads(r.read().decode("utf-8"))
        assert http_dl["source"] == "zpdf" and http_dl["code"] == "es"
    finally:
        srv.shutdown()
        srv.server_close()


def test_build_langpack_bundles_argos_models(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_langpack", Path(__file__).resolve().parent.parent / "packaging" / "build_langpack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    az = _make_argos_zip(tmp_path / "en_es.argosmodel", "en", "es", "English", "Spanish")
    ui_json = Path(__file__).resolve().parent.parent / "pdfmanager" / "locales" / "es.json"
    zip_path, sha_path = mod.build_pack("es", ui_json, tmp_path / "out", version="0.60.2", argos_zips=[az])
    assert zip_path.is_file() and sha_path.is_file()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
    assert "models/en-es/model.bin" in names
    assert "models/en-es/sentencepiece.model" in names
    assert not any("stanza" in n for n in names)


def test_download_size_and_byte_progress(tmp_path, monkeypatch):
    import io
    import urllib.request
    from pdfmanager import progress

    assert i18n._fmt_bytes(500) == "500 B"
    assert i18n._fmt_bytes(12_500) == "12 KB"
    assert i18n._fmt_bytes(645_000_000) == "645.0 MB"
    assert i18n._fmt_bytes(1_400_000_000) == "1.40 GB"

    # Verify byte-level progress updates inside a progress.Job
    payload = b"x" * 600_000

    class FakeResp(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.headers = {"Content-Length": str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=600: FakeResp(payload))
    dest = tmp_path / "model.bin"
    seen = []
    job = progress.Job()
    orig_report = progress.report

    def spy_report(*args, **kwargs):
        seen.append((kwargs.get("message"), kwargs.get("fraction_in_part")))
        return orig_report(*args, **kwargs)

    monkeypatch.setattr(progress, "report", spy_report)
    job.run(
        i18n._http_download_to_file,
        "https://example.invalid/model.bin",
        dest,
        progress_label="Downloading NLLB-200",
        frac_start=0.1,
        frac_end=0.9,
    )
    assert job.state == "done"
    assert dest.read_bytes() == payload
    assert any("Downloading NLLB-200" in (m or "") and "%" in (m or "") for m, _ in seen)
    assert seen[-1][1] == pytest.approx(0.9)
