# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Tests for offline translation runtime auto-provisioning and BPE tokenizer support."""

import sys

import pytest

from pdfmanager import core, i18n, policy, translate, update


def test_ensure_runtime_returns_true_when_already_installed(monkeypatch):
    monkeypatch.setattr(i18n, "_has_ct2_runtime", lambda: True)
    called = []
    monkeypatch.setattr(i18n.subprocess, "run", lambda *a, **kw: called.append(a))
    assert translate.ensure_runtime(force=True) is True
    assert not called


def test_ensure_runtime_auto_installs_via_pip_when_missing(monkeypatch):
    installed = {"ok": False}
    monkeypatch.setattr(i18n, "_has_ct2_runtime", lambda: installed["ok"])
    monkeypatch.setattr(policy, "get", lambda key, default=None, **kw: False)
    monkeypatch.setattr(update, "settings", lambda: {"online": True})
    monkeypatch.setattr(i18n.importlib.util, "find_spec", lambda name: object() if name == "pip" else None)

    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        installed["ok"] = True
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(i18n.subprocess, "run", fake_run)
    assert translate.ensure_runtime(force=True) is True
    assert ran and "ctranslate2>=4.0" in ran[0] and "sentencepiece>=0.2" in ran[0]


def test_ensure_runtime_respects_offline_policy_and_frozen(monkeypatch):
    monkeypatch.setattr(i18n, "_has_ct2_runtime", lambda: False)
    called = []
    monkeypatch.setattr(i18n.subprocess, "run", lambda *a, **kw: called.append(a))

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


def test_clean_mt_output_strips_sentencepiece_marker_and_cjk_spaces():
    assert translate._clean_mt_output("\u2581你好, 你今天好吗?", "zh-Hans") == "你好, 你今天好吗?"
    assert translate._clean_mt_output("\u2581本文件 解释了 协议 的 条款。", "zh-Hans") == "本文件解释了协议的条款。"
    assert translate._clean_mt_output("\u2581Hello   world", "en") == "Hello world"


def test_extract_paragraphs_preserves_headings_bullets_and_reflows_prose():
    raw = (
        "Invoice INV-2026-118\n"
        "Please sign and return this agree-\n"
        "ment before December 31, 2026.\n"
        "- First item\n"
        "- Second item\n"
    )
    paras = translate._extract_paragraphs(raw)
    assert paras == [
        "Invoice INV-2026-118",
        "Please sign and return this agreement before December 31, 2026.",
        "- First item",
        "- Second item",
    ]


def test_neural_translator_skips_ztok_placeholders():
    seen = []

    def neural_fn(batch):
        seen.extend(batch)
        return batch

    neural_fn._neural = True
    out = translate.translate_texts(
        ["Please pay $1,250.00 before 2026-12-31."],
        "en",
        "zh-Hans",
        translator=neural_fn,
    )
    assert out == ["Please pay $1,250.00 before 2026-12-31."]
    assert all("__ZTOK" not in s for s in seen)


def test_cjk_wrap_text_breaks_long_chinese_lines():
    from pdfmanager import fonts

    text = "本文件详细解释了协议的所有条款和条件请在截止日期前签署并归还本协议以便及时处理您的申请。"
    font_name = fonts.font_for(text)
    lines = translate._wrap_text(text, font_name, 10, 120)
    assert len(lines) >= 3
    assert "".join(lines) == text


def test_layout_preserving_translation_keeps_images_columns_and_styles(tmp_path):
    from PIL import Image
    from pypdf import PdfReader
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    import pdfplumber

    src = tmp_path / "formatted.pdf"
    logo = tmp_path / "logo.png"
    Image.new("RGB", (40, 40), (20, 90, 180)).save(logo)

    c = canvas.Canvas(str(src), pagesize=(612, 792))
    # Colored header banner + logo image
    c.setFillColorRGB(0.1, 0.2, 0.45)
    c.rect(36, 700, 540, 52, stroke=0, fill=1)
    c.drawImage(ImageReader(str(logo)), 46, 706, width=40, height=40)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(100, 720, "Quarterly Summary")
    # Two separate columns
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 10)
    c.drawString(40, 640, "Left column paragraph line one.")
    c.drawString(320, 640, "Right column paragraph line one.")
    c.showPage()
    c.save()

    with pdfplumber.open(str(src)) as pdoc:
        blocks = translate._extract_layout_blocks(pdoc.pages[0])
    # Heading + Left column + Right column must remain 3 distinct positioned blocks
    assert len(blocks) == 3
    xs = sorted(b["x0"] for b in blocks)
    assert xs[0] < 60 and xs[1] < 120 and xs[2] > 300
    heading = next(b for b in blocks if "Quarterly" in b["text"])
    assert heading["size"] >= 17.0

    def fake_zh(batch):
        mapping = {
            "Quarterly Summary": "季度总结",
            "Left column paragraph line one.": "左栏第一行段落。",
            "Right column paragraph line one.": "右栏第一行段落。",
        }
        return [mapping.get(s, s) for s in batch]

    fake_zh._neural = True

    out_inp = tmp_path / "translated_inplace.pdf"
    translate.translate_pdf(src, out_inp, "en", "zh-Hans", mode="in-place", translator=fake_zh)
    reader = PdfReader(str(out_inp))
    page = reader.pages[0]
    # Embedded image must still be present in the page resources!
    xobjs = page.get("/Resources", {}).get("/XObject", {})
    if hasattr(xobjs, "get_object"):
        xobjs = xobjs.get_object()
    has_image = any(
        (v.get_object() if hasattr(v, "get_object") else v).get("/Subtype") == "/Image"
        for v in xobjs.values()
    )
    assert has_image, "Original embedded image was lost during in-place translation!"
    text = page.extract_text() or ""
    assert "季度总结" in text and "左栏第一行段落" in text and "右栏第一行段落" in text


def test_neural_entity_protection_and_verbatim_bypass():
    called_with = []

    def fake_neural(batch):
        called_with.extend(batch)
        return [s.replace("Contact", "联系").replace("or visit", "或访问") for s in batch]

    fake_neural._neural = True

    inputs = [
        "support@example.com",
        "$1,250.00",
        "(555) 123-4567",
        "2026-12-31",
        "INV-2026-118",
        "Contact support@example.com or visit https://example.com.",
    ]
    out = translate.translate_texts(inputs, "en", "zh-Hans", translator=fake_neural)
    # First 5 pure-data blocks bypass the neural model completely
    assert out[:5] == inputs[:5]
    assert len(called_with) == 1
    assert "910001" in called_with[0] and "910002" in called_with[0]
    assert out[5] == "联系 support@example.com 或访问 https://example.com."


def test_neural_entity_fallback_when_sentinel_dropped():
    def dropping_neural(batch):
        out = []
        for s in batch:
            if "910001" in s:
                out.append("联系丢失了")  # simulates model dropping sentinel
            else:
                out.append(s.replace("Email", "电邮").replace("today", "今天"))
        return out

    dropping_neural._neural = True
    res = translate.translate_texts(["Email support@example.com today"], "en", "zh-Hans", translator=dropping_neural)
    assert "support@example.com" in res[0] and "电邮" in res[0] and "今天" in res[0]



