# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for Priority #7 Phase 4: Language-pack OCR models and multilingual search by meaning."""

import importlib.util
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from reportlab.pdfgen import canvas

from pdfmanager import appdata, i18n, meaning, ocr, search

ROOT = Path(__file__).resolve().parent.parent


def _load_build_langpack():
    spec = importlib.util.spec_from_file_location("build_langpack", ROOT / "packaging" / "build_langpack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_words_npz(path, word_vectors):
    """Build a valid ``words.npz`` file mapping ``{word: 128-d float32 vector}``."""
    pieces = []
    joins = []
    vecs = []
    seen_joins = set()
    for word, vec in word_vectors.items():
        piece = "\u2581" + word
        pieces.append(piece)
        vecs.append(np.asarray(vec, dtype=np.float32))
        # Add BPE joins so ``_pieces(word)`` merges the letters into ``▁word``.
        cur = list(piece)
        while len(cur) > 1:
            pair = f"{cur[0]} {cur[1]}"
            if pair not in seen_joins:
                seen_joins.add(pair)
                joins.append(pair)
            cur[:2] = [cur[0] + cur[1]]
    mat = np.stack(vecs).astype(np.float32)
    scale = np.maximum(np.max(np.abs(mat), axis=1), 1e-6).astype(np.float32)
    q = np.round(np.clip(mat / scale[:, None], -1.0, 1.0) * 127).astype(np.int8)
    np.savez_compressed(
        path,
        vectors=q,
        scale=scale,
        pieces=np.frombuffer(json.dumps(pieces, ensure_ascii=False).encode("utf-8"), dtype=np.uint8),
        joins=np.frombuffer(json.dumps(joins, ensure_ascii=False).encode("utf-8"), dtype=np.uint8),
    )


def _make_pdf(path, pages):
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    for text in pages:
        c.setFont("Helvetica", 12)
        c.drawString(72, 760, text)
        c.showPage()
    c.save()


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path)
    meaning.reset()
    yield tmp_path
    meaning.reset()


def test_pack_install_extracts_ocr_and_meaning_assets(isolated_appdata, tmp_path):
    builder = _load_build_langpack()
    ocr_src = tmp_path / "ocr_src"
    ocr_src.mkdir()
    (ocr_src / "rec.onnx").write_bytes(b"FAKE-ONNX-MODEL")
    (ocr_src / "dict.txt").write_text("a\nb\nc\n", encoding="utf-8")

    vec = np.zeros(meaning.SIZE, dtype=np.float32)
    vec[0] = 1.0
    npz_path = tmp_path / "words.npz"
    _make_words_npz(npz_path, {"bonjour": vec})

    zip_path, sha_path = builder.build_pack(
        "fr",
        ROOT / "pdfmanager" / "locales" / "fr.json",
        tmp_path / "dist",
        ocr_dir=ocr_src,
        meaning_npz=npz_path,
    )
    digest = sha_path.read_text(encoding="ascii").split()[0]
    res = i18n.install_pack_from_zip(zip_path, expected_sha256=digest)
    assert res["code"] == "fr"
    assert "ocr/rec.onnx" in res["assets"]
    assert "meaning/words.npz" in res["assets"]

    packs = {p["code"]: p for p in i18n.list_installed_packs()}
    assert packs["fr"]["has_ocr"] is True
    assert packs["fr"]["has_meaning"] is True
    assert ocr.pack_ocr_model(language="fr") == isolated_appdata / "locales" / "fr" / "ocr" / "rec.onnx"
    assert ocr.pack_ocr_model(language="fra") == isolated_appdata / "locales" / "fr" / "ocr" / "rec.onnx"


def test_pack_install_ignores_path_traversal_assets(isolated_appdata, tmp_path):
    zip_path = tmp_path / "evil.zip"
    en_ui = json.loads((ROOT / "pdfmanager" / "locales" / "en.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"code": "es", "name": "Español", "version": "1.0"}))
        zf.writestr("ui.json", json.dumps(en_ui))
        zf.writestr("ocr/../../escaped.onnx", b"BAD")
    res = i18n.install_pack_from_zip(zip_path)
    assert res["assets"] == []
    assert not (isolated_appdata / "escaped.onnx").exists()


def test_ocr_script_for_accepts_bcp47_and_uses_pack_model_offline(isolated_appdata, monkeypatch):
    assert ocr.script_for("fr") is None
    assert ocr.script_for("ar") == "arabic"
    assert ocr.script_for("ru") == "eslav"

    ar_ocr = isolated_appdata / "locales" / "ar" / "ocr"
    ar_ocr.mkdir(parents=True)
    bundled = ar_ocr / "arabic.onnx"
    bundled.write_bytes(b"FAKE-ARABIC-ONNX")

    # Ensure no network fetch is attempted when the pack provides the model.
    monkeypatch.setattr(ocr, "_download_model", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not download")))
    assert ocr._language_model("arabic") == bundled


def test_multilingual_search_by_meaning_with_spanish_pack(isolated_appdata, tmp_path):
    # Create a Spanish + cross-lingual English word table in an installed 'es' pack.
    v_invoice = np.zeros(meaning.SIZE, dtype=np.float32)
    v_invoice[1] = 1.0
    v_recipe = np.zeros(meaning.SIZE, dtype=np.float32)
    v_recipe[2] = 1.0

    es_meaning = isolated_appdata / "locales" / "es" / "meaning"
    es_meaning.mkdir(parents=True)
    _make_words_npz(es_meaning / "words.npz", {
        "factura": v_invoice,
        "pago": v_invoice,
        "saldo": v_invoice,
        "invoice": v_invoice,
        "receta": v_recipe,
        "cocina": v_recipe,
        "sopa": v_recipe,
    })
    meaning.reset()
    assert meaning.table_version() != meaning.VERSION

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    _make_pdf(docs_dir / "documento.pdf", [
        "Resumen de factura mensual y saldo pendiente de pago",
        "Instrucciones de receta de cocina para sopa casera",
    ])

    # Spanish query finds page 1 (invoice/payment page)
    es_res = search.search(docs_dir, "pago factura", how="best")
    assert es_res["meaning"] is True
    assert es_res["matches"]
    assert es_res["matches"][0]["page"] == 1

    # Cross-lingual English query finds the Spanish invoice page
    en_res = search.search(docs_dir, "invoice", how="best")
    assert en_res["meaning"] is True
    assert en_res["matches"]
    assert en_res["matches"][0]["page"] == 1

    # Removing the pack restores the base English table version
    i18n.remove_pack("es")
    assert meaning.table_version() == meaning.VERSION
