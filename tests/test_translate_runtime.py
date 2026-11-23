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
    assert translate._clean_mt_output("\u2581你好, 你今天好吗?", "zh-Hans") == "你好，你今天好吗？"
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
    # Original English text must be stripped from the content stream (no ghost text on Copy/Find!)
    assert "Quarterly Summary" not in text and "Left column" not in text
    assert heading["bold"] is True


def test_table_cells_and_centered_title_layout(tmp_path):
    from reportlab.pdfgen import canvas
    import pdfplumber

    src = tmp_path / "table_and_title.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(306, 740, "Centered Report Title")
    # Draw a 2x2 bordered table
    c.setStrokeColorRGB(0, 0, 0)
    c.rect(60, 620, 480, 60, stroke=1, fill=0)
    c.line(300, 620, 300, 680)
    c.line(60, 650, 540, 650)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(70, 660, "Item")
    c.drawString(310, 660, "Price")
    c.setFont("Helvetica", 10)
    c.drawString(70, 630, "Widget")
    c.drawRightString(530, 630, "$99.00")
    c.showPage()
    c.save()

    with pdfplumber.open(str(src)) as pdoc:
        blocks = translate._extract_layout_blocks(pdoc.pages[0])
    title = next(b for b in blocks if "Centered Report Title" in b["text"])
    assert title["align"] == "center" and title["bold"] is True
    table_blocks = [b for b in blocks if b.get("in_table")]
    assert len(table_blocks) == 4
    price_val = next(b for b in table_blocks if "$99.00" in b["text"])
    assert price_val["align"] == "right"


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


def test_nllb200_mapping_and_catalog_entry(tmp_path, monkeypatch):
    from pdfmanager import appdata

    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    assert translate.bcp47_to_flores("en") == "eng_Latn"
    assert translate.bcp47_to_flores("zh-Hans") == "zho_Hans"
    assert translate.bcp47_to_flores("zh-Hant") == "zho_Hant"
    assert translate.bcp47_to_flores("es") == "spa_Latn"
    assert translate.bcp47_to_flores("ja") == "jpn_Jpan"
    assert translate.nllb_model_dir() is None

    catalog = i18n.list_catalog_packs(fetch=False)
    assert catalog["packs"] and catalog["packs"][0]["code"] == "nllb-200"
    assert catalog["packs"][0]["installed"] is False

    # Simulate installed NLLB-200 model
    mdir = i18n.packs_dir() / "nllb-200" / "models" / "nllb-200"
    mdir.mkdir(parents=True)
    (mdir / "model.bin").write_bytes(b"fake")
    (mdir / "sentencepiece.bpe.model").write_bytes(b"fake")
    assert translate.nllb_model_dir() == mdir

    catalog2 = i18n.list_catalog_packs(fetch=False)
    assert catalog2["packs"][0]["installed"] is True

    # _load_offline_translator prefers NLLB-200 automatically when installed
    called = []
    monkeypatch.setattr(
        translate,
        "_load_nllb_translator",
        lambda s, d, p: (called.append((s, d, p)) or (lambda batch: batch)),
    )
    translate._load_offline_translator("en", "zh-Hans")
    assert called == [("en", "zh-Hans", mdir)]


def test_bordered_page_box_does_not_flatten_paragraphs(tmp_path):
    """A full-page or section border rectangle must NOT be treated as a single
    table cell that smashes headings and paragraphs together."""
    from reportlab.pdfgen import canvas
    import pdfplumber

    src = tmp_path / "bordered.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    # Outer page border box (common in resumes, certificates, invoices)
    c.rect(36, 36, 540, 720, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, 700, "Section Heading")
    c.setFont("Helvetica", 10)
    c.drawString(60, 660, "First paragraph inside the bordered page.")
    c.drawString(60, 600, "Second paragraph separated by whitespace.")
    c.showPage()
    c.save()

    with pdfplumber.open(str(src)) as pdoc:
        blocks = translate._extract_layout_blocks(pdoc.pages[0])
    assert len(blocks) == 3
    assert all(not b.get("in_table") for b in blocks)
    assert blocks[0]["text"] == "Section Heading" and blocks[0]["bold"] is True


def test_llm_translator_batch_and_fallback(monkeypatch):
    calls = []

    def fake_post(url, payload, headers=None, timeout=180):
        import json
        calls.append((url, payload["model"], headers))
        items = json.loads(payload["messages"][1]["content"])
        if len(items) == 2:
            return {"choices": [{"message": {"content": '```json\n["你好世界", "联系 support@example.com"]\n```'}}]}
        return {"choices": [{"message": {"content": '["单项翻译"]'}}]}

    monkeypatch.setattr(i18n, "_http_post_json", fake_post)
    fn = translate._load_llm_translator(
        "en", "zh-Hans",
        api_url="http://localhost:11434/v1",
        model="qwen2.5:7b",
        api_key="",
    )
    out = translate.translate_texts(
        ["Hello world", "Contact support@example.com", "$1,250.00"],
        "en", "zh-Hans",
        translator=fn,
    )
    assert out[0] == "你好世界"
    assert out[1] == "联系 support@example.com"
    assert out[2] == "$1,250.00"  # pure currency bypassed LLM verbatim
    assert calls and calls[0][0] == "http://localhost:11434/v1/chat/completions"
    assert calls[0][1] == "qwen2.5:7b"


def test_abbreviation_and_list_safe_sentence_splitting():
    text = "Dr. Smith met Ms. Jones at Acme Inc. in the U.S. on Jan. 5. See Fig. 2 for details."
    sents = translate.split_sentences(text)
    assert sents == [
        "Dr. Smith met Ms. Jones at Acme Inc. in the U.S. on Jan. 5.",
        "See Fig. 2 for details.",
    ]
    numbered = translate.split_sentences("1. First step is easy. Second sentence.")
    assert numbered == ["1. First step is easy.", "Second sentence."]


def test_all_caps_normalization_and_structural_affixes():
    assert translate._normalize_all_caps("EXECUTIVE SUMMARY OF PDF REPORT") == "Executive summary of PDF report"
    assert translate._normalize_all_caps("TERMS AND CONDITIONS") == "Terms and conditions"
    assert translate._normalize_all_caps("Mixed Case Heading") == "Mixed Case Heading"

    seen = []

    def fake_neural(batch):
        seen.extend(batch)
        return [f"ZH({s})" for s in batch]

    fake_neural._neural = True
    out = translate.translate_texts(
        [
            "EXECUTIVE SUMMARY",
            "• Review the contract",
            "Email: alice@acme.com",
            "Total: $1,250.00",
        ],
        "en",
        "zh-Hans",
        translator=fake_neural,
        glossary="",
    )
    assert seen == ["Executive summary", "Review the contract", "Email", "Total"]
    assert out == [
        "ZH(Executive summary)",
        "• ZH(Review the contract)",
        "ZH(Email)：alice@acme.com",
        "ZH(Total)：$1,250.00",
    ]


def test_nllb_paragraph_context_and_13b_preference(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "packs_dir", lambda: tmp_path / "locales")
    catalog = i18n.list_catalog_packs(fetch=False)
    codes = [p["code"] for p in catalog["packs"]]
    assert "nllb-200" in codes
    assert "nllb-200-1.3b" in codes

    # Install 600M first
    m600 = i18n.packs_dir() / "nllb-200" / "models" / "nllb-200"
    m600.mkdir(parents=True)
    (m600 / "model.bin").write_bytes(b"600m")
    (m600 / "sentencepiece.bpe.model").write_bytes(b"sp")
    assert translate.nllb_model_dir() == m600

    # Installing 1.3B takes priority automatically
    m13b = i18n.packs_dir() / "nllb-200-1.3b" / "models" / "nllb-200-1.3b"
    m13b.mkdir(parents=True)
    (m13b / "model.bin").write_bytes(b"1.3b")
    (m13b / "sentencepiece.bpe.model").write_bytes(b"sp")
    assert translate.nllb_model_dir() == m13b

    # Context chunking groups short consecutive sentences together for NLLB
    received = []

    def fake_nllb(batch):
        received.extend(batch)
        return [f"TR:{s}" for s in batch]

    fake_nllb._neural = True
    fake_nllb._context_window = 360
    out = translate.translate_texts(
        ["First sentence is short. It continues with a pronoun."],
        "en",
        "es",
        translator=fake_nllb,
        glossary="",
    )
    assert received == ["First sentence is short. It continues with a pronoun."]
    assert out == ["TR:First sentence is short. It continues with a pronoun."]


def test_offline_glossary_keep_verbatim_and_force_map():
    glossary = "# Company terms\nAcme Corp\nForce Majeure = 不可抗力\nStatement of Work -> 工作说明书\n"
    rules = translate.parse_glossary(glossary)
    assert rules[0] == ("Statement of Work", "工作说明书")
    assert ("Force Majeure", "不可抗力") in rules
    assert ("Acme Corp", None) in rules

    def fake_neural(batch):
        return [f"译文:{s}" for s in batch]

    fake_neural._neural = True
    out = translate.translate_texts(
        ["Acme Corp signed the Statement of Work under Force Majeure."],
        "en",
        "zh-Hans",
        translator=fake_neural,
        glossary=glossary,
    )
    assert "Acme Corp" in out[0]
    assert "工作说明书" in out[0]
    assert "不可抗力" in out[0]


def test_bucketed_ct2_decoding_and_hallucination_retry():
    calls = []

    class FakeRes:
        def __init__(self, hyp):
            self.hypotheses = [hyp]

    class FakeTranslator:
        def translate_batch(self, batch_toks, **kwargs):
            calls.append((len(batch_toks), kwargs))
            if kwargs["beam_size"] == 4 and len(batch_toks[0]) <= 16:
                return [FakeRes(["halluc"] * 40)]
            return [FakeRes(["ok", "short"]) for _ in batch_toks]

    hyps = translate._translate_ct2_bucketed(FakeTranslator(), [["Summary", "</s>"]])
    assert hyps == [["ok", "short"]]
    assert calls[0][1]["repetition_penalty"] == 1.2
    assert calls[0][1]["max_decoding_length"] <= 64
    assert len(calls) == 2
    assert calls[1][1]["beam_size"] == 2
    assert calls[1][1]["repetition_penalty"] == 1.35


def test_cjk_and_latin_punctuation_polish():
    assert translate._clean_mt_output("你好 , 世界 .", "zh-Hans") == "你好，世界。"
    assert translate._clean_mt_output("Hello , world ( test ) !", "en") == "Hello, world (test)!"





