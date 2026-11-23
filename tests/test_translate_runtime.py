# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for offline translation runtime auto-provisioning and BPE tokenizer support."""

import sys

import pytest

from pdfmanager import core, policy, translate, update


def test_ensure_runtime_returns_true_when_already_installed(monkeypatch):
    monkeypatch.setattr(translate, "_has_ct2_runtime", lambda: True)
    called = []
    monkeypatch.setattr(translate.subprocess, "run", lambda *a, **kw: called.append(a))
    assert translate.ensure_runtime(force=True) is True
    assert not called


def test_ensure_runtime_auto_installs_via_pip_when_missing(monkeypatch):
    installed = {"ok": False}
    monkeypatch.setattr(translate, "_has_ct2_runtime", lambda: installed["ok"])
    monkeypatch.setattr(policy, "get", lambda key, default=None, **kw: False)
    monkeypatch.setattr(update, "settings", lambda: {"online": True})
    monkeypatch.setattr(translate.importlib.util, "find_spec", lambda name: object() if name == "pip" else None)

    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        installed["ok"] = True
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(translate.subprocess, "run", fake_run)
    assert translate.ensure_runtime(force=True) is True
    assert ran and "ctranslate2>=4.0" in ran[0] and "sentencepiece>=0.2" in ran[0]


def test_ensure_runtime_respects_offline_policy_and_frozen(monkeypatch):
    monkeypatch.setattr(translate, "_has_ct2_runtime", lambda: False)
    called = []
    monkeypatch.setattr(translate.subprocess, "run", lambda *a, **kw: called.append(a))

    # Enterprise disable_network policy blocks pip
    monkeypatch.setattr(policy, "get", lambda key, default=None, **kw: key == "disable_network")
    monkeypatch.setattr(update, "settings", lambda: {"online": True})
    assert translate.ensure_runtime(force=True) is False
    assert not called

    # User offline privacy toggle blocks pip
    monkeypatch.setattr(policy, "get", lambda key, default=None, **kw: False)
    monkeypatch.setattr(update, "settings", lambda: {"online": False})
    assert translate.ensure_runtime(force=True) is False
    assert not called

    # Frozen standalone bundle never runs pip
    monkeypatch.setattr(update, "settings", lambda: {"online": True})
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert translate.ensure_runtime(force=True) is False
    assert not called


def test_subword_bpe_encode_decode(tmp_path):
    bpe_file = tmp_path / "bpe.model"
    bpe_file.write_text(
        "#version: 0.2\n"
        "h e\n"
        "l l\n"
        "he ll\n"
        "hell o</w>\n"
        "w o\n"
        "r l\n"
        "rl d</w>\n",
        encoding="utf-8",
    )
    tok = translate._load_tokenizer(bpe_file, spm=None)
    assert isinstance(tok, translate._SubwordBpe)
    tokens = tok.encode("hello world!")
    assert tokens == ["hello", "wo@@", "rld", "!"]
    assert tok.decode(tokens) == "hello world!"
    assert tok.decode(["S@@", "pan@@", "ish", ",", "please", "."]) == "Spanish, please."


def test_missing_tokenizer_reports_specific_error(tmp_path, monkeypatch):
    mdir = tmp_path / "en-es"
    mdir.mkdir()
    (mdir / "model.bin").write_bytes(b"fake")

    # Pretend ctranslate2 and sentencepiece are importable
    fake_mod = type("FakeMod", (), {})()
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_mod)
    monkeypatch.setitem(sys.modules, "sentencepiece", fake_mod)

    with pytest.raises(core.PdfManagerError, match="no tokenizer"):
        translate._load_single_translator("en", "es", mdir)
