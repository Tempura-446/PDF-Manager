# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Priority #7 Phase 2: Starter language packs (zh-Hans, es, fr, de) and pack builder validation."""
import importlib.util
import json
from pathlib import Path

import pytest

from pdfmanager import appdata, i18n, update

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "pdfmanager" / "locales"


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("zpdf_build_langpack", ROOT / "packaging" / "build_langpack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("code", ["zh-Hans", "es", "fr", "de"])
def test_starter_locales_have_complete_keys_and_placeholder_parity(code):
    data = json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))
    errors = i18n.validate_catalog(data)
    assert errors == [], f"{code}: {errors}"
    assert i18n.t("status.pages_count", lang=code, count=5) != "status.pages_count"
    assert "5" in i18n.t("status.pages_count", lang=code, count=5)


def test_validate_catalog_catches_placeholder_mismatch():
    bad = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    bad["status.pages_count"] = "pages without placeholder"
    errors = i18n.validate_catalog(bad)
    assert any("placeholder mismatch" in e for e in errors)


def test_build_langpack_and_install_roundtrip(builder, tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(update, "data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)

    zip_path, sha_path = builder.build_pack("zh-Hans", LOCALES / "zh-Hans.json", tmp_path / "dist", version="0.59.0")
    assert zip_path.name == "ZPDF-lang-zh-Hans-0.59.0.zip"
    digest = sha_path.read_text(encoding="ascii").split()[0]

    installed = i18n.install_pack_from_zip(zip_path, expected_sha256=digest)
    assert installed["code"] == "zh-Hans" and installed["name"] == "简体中文"
    assert i18n.set_language("zh-Hans") == "zh-Hans"
    assert i18n.t("reader.save") == "保存"

    langs = {x["code"] for x in i18n.list_available_languages()}
    assert {"en", "zh-Hans", "es", "fr", "de"} <= langs
