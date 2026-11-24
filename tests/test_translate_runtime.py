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


def test_madlad400_mapping_and_catalog_entry(tmp_path, monkeypatch):
    from pdfmanager import appdata

    monkeypatch.setattr(appdata, "data_dir", lambda: tmp_path / "appdata")
    assert translate.bcp47_to_madlad("en") == "en"
    assert translate.bcp47_to_madlad("zh-Hans") == "zh"
    assert translate.bcp47_to_madlad("zh-Hant") == "zh_Hant"
    assert translate.bcp47_to_madlad("es") == "es"
    assert translate.bcp47_to_madlad("ja") == "ja"
    assert translate.madlad_model_dir() is None

    catalog = i18n.list_catalog_packs(fetch=False)
    assert catalog["packs"] and catalog["packs"][0]["code"] == "madlad-400-3b"
    assert catalog["packs"][0]["license"] == "Apache-2.0"
    assert catalog["packs"][0]["installed"] is False
    # Ensure no CC-BY-NC NLLB packs remain in catalog
    assert not any("nllb" in p["code"].lower() for p in catalog["packs"])

    # Simulate installed MADLAD-400 3B model
    mdir = i18n.packs_dir() / "madlad-400-3b" / "models" / "madlad-400-3b"
    mdir.mkdir(parents=True)
    (mdir / "model.bin").write_bytes(b"fake")
    (mdir / "spiece.model").write_bytes(b"fake")
    assert translate.madlad_model_dir() == mdir

    catalog2 = i18n.list_catalog_packs(fetch=False)
    assert catalog2["packs"][0]["installed"] is True

    # _load_offline_translator prefers MADLAD-400 3B automatically when installed
    called = []
    monkeypatch.setattr(
        translate,
        "_load_madlad_translator",
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


def test_madlad_paragraph_context_and_opus_mt_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "packs_dir", lambda: tmp_path / "locales")
    catalog = i18n.list_catalog_packs(fetch=False)
    codes = [p["code"] for p in catalog["packs"]]
    assert "madlad-400-3b" in codes
    assert "opus-es" in codes
    assert "opus-fr" in codes
    assert "opus-de" in codes

    # Context chunking groups short consecutive sentences together for MADLAD-400
    received = []

    def fake_madlad(batch):
        received.extend(batch)
        return [f"TR:{s}" for s in batch]

    fake_madlad._neural = True
    fake_madlad._context_window = 360
    out = translate.translate_texts(
        ["First sentence is short. It continues with a pronoun."],
        "en",
        "es",
        translator=fake_madlad,
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


def test_ct2_model_lru_cache_and_invalidation(tmp_path):
    translate.clear_model_cache()
    mdir = tmp_path / "m1"
    mdir.mkdir()
    mbin = mdir / "model.bin"
    mbin.write_bytes(b"v1")
    sp_path = mdir / "bpe.model"
    sp_path.write_text("#version: 0.2\na b\n", encoding="utf-8")

    loads = []

    class FakeCT2:
        class Translator:
            def __init__(self, path, device="cpu"):
                loads.append(path)

    class FakeSPM:
        pass

    tr1, _, _ = translate._get_cached_ct2(mdir, sp_path, sp_path, FakeCT2, FakeSPM)
    tr2, _, _ = translate._get_cached_ct2(mdir, sp_path, sp_path, FakeCT2, FakeSPM)
    assert tr1 is tr2
    assert len(loads) == 1
    translate.clear_model_cache()


def test_detect_language_scripts_and_latin():
    assert translate.detect_language("这是一份关于合同条款的简体中文文件。") == "zh-Hans"
    assert translate.detect_language("這是一份關於合約條款的繁體中文文件，請仔細閱讀。") == "zh-Hant"
    assert translate.detect_language("これは日本語のテスト文書です。") == "ja"
    assert translate.detect_language("이것은 한국어 테스트 문서입니다.") == "ko"
    assert translate.detect_language("Это документ на русском языке для проверки.") == "ru"
    assert translate.detect_language("Це український документ із перевіркою ї та є.") == "uk"
    assert translate.detect_language("هذا مستند باللغة العربية للاختبار.") == "ar"
    assert translate.detect_language("El contrato de servicios para la empresa con el cliente está vigente.") == "es"
    assert translate.detect_language("Le contrat de service pour la société avec le client est en vigueur.") == "fr"
    assert translate.detect_language("Der Vertrag für das Unternehmen und die Kunden ist nicht gültig.") == "de"
    assert translate.detect_language("This agreement sets forth the terms and conditions for the services.") == "en"


def test_translate_pdf_auto_detect_and_empty_page_count(tmp_path):
    from reportlab.pdfgen import canvas

    src = tmp_path / "es_and_blank.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "El contrato para la empresa con el cliente está vigente.")
    c.showPage()
    # Second page has no text (simulates scanned image page)
    c.rect(72, 600, 100, 100, stroke=1, fill=0)
    c.showPage()
    c.save()

    out = tmp_path / "out.pdf"
    res = translate.translate_pdf(
        src, out, "auto", "en",
        mode="in-place",
        translator=lambda batch: [f"EN:{s}" for s in batch],
    )
    assert res["src_lang"] == "es"
    assert res["empty_pages"] == 1
    assert out.is_file()


def test_vertical_headroom_prevents_clipping_longer_translation():
    from pypdf import PdfWriter

    # Narrow 1-line block with plenty of whitespace below (next block is 120pt lower)
    blocks = [
        {
            "text": "Short",
            "x0": 72.0, "top": 100.0, "x1": 150.0, "bottom": 114.0,
            "box_x0": 72.0, "box_x1": 150.0, "size": 12.0,
            "color": (0.0, 0.0, 0.0), "family": "sans", "bold": False,
            "italic": False, "align": "left", "lines": 1, "in_table": False,
        },
        {
            "text": "Next section",
            "x0": 72.0, "top": 240.0, "x1": 200.0, "bottom": 254.0,
            "box_x0": 72.0, "box_x1": 200.0, "size": 12.0,
            "color": (0.0, 0.0, 0.0), "family": "sans", "bold": False,
            "italic": False, "align": "left", "lines": 1, "in_table": False,
        },
    ]
    long_trans = "This is a much longer expanded translation that wraps onto a second line"
    overlay = translate._make_layout_overlay(612.0, 792.0, blocks, [long_trans, "Next section"])
    extracted = overlay.extract_text() or ""
    assert "second line" in extracted


def test_code_and_math_blocks_preserved_verbatim(tmp_path):
    assert translate._is_code_or_math_block({"family": "mono", "text": "def foo(x): return x + 1"}) is True
    assert translate._is_code_or_math_block({"family": "sans", "text": "∑ α_i = β · √n"}) is True
    assert translate._is_code_or_math_block({"family": "sans", "text": "Normal contract paragraph."}) is False

    from reportlab.pdfgen import canvas

    src = tmp_path / "code_doc.pdf"
    c = canvas.Canvas(str(src), pagesize=(612, 792))
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Regular prose sentence.")
    c.setFont("Courier", 10)
    c.drawString(72, 660, "def compute_total(a, b): return a + b;")
    c.showPage()
    c.save()

    seen = []

    def fake_tr(batch):
        seen.extend(batch)
        return [f"ES:{s}" for s in batch]

    out = tmp_path / "code_out.pdf"
    translate.translate_pdf(src, out, "en", "es", mode="in-place", translator=fake_tr)
    assert any("Regular prose" in s for s in seen)
    assert not any("compute_total" in s for s in seen)


def test_bold_prefix_rotated_text_and_rtl_shaping():
    words_bp = [
        {"text": "Note:", "fontname": "Helvetica-Bold"},
        {"text": "Keep", "fontname": "Helvetica"},
        {"text": "safe.", "fontname": "Helvetica"},
    ]
    assert translate._detect_bold_prefix(words_bp, block_bold=False) is True
    assert translate._detect_bold_prefix(words_bp, block_bold=True) is False
    assert translate._detect_rotation([{"upright": False, "direction": "btt"}]) == 90
    assert translate._detect_rotation([{"upright": False, "direction": "ttb"}]) == 270
    assert translate._detect_rotation([{"upright": True}]) == 0

    shaped = translate._shape_rtl("مرحبا 2026", "ar")
    assert "2026" in shaped
    assert shaped != ""

    blocks = [
        {
            "text": "Note: Keep safe.",
            "x0": 72.0, "top": 100.0, "x1": 300.0, "bottom": 114.0,
            "box_x0": 72.0, "box_x1": 300.0, "size": 11.0,
            "color": (0.0, 0.0, 0.0), "family": "sans", "bold": False,
            "italic": False, "bold_prefix": True, "rotation": 0,
            "align": "left", "lines": 1, "in_table": False,
        },
        {
            "text": "CONFIDENTIAL",
            "x0": 20.0, "top": 200.0, "x1": 34.0, "bottom": 360.0,
            "box_x0": 20.0, "box_x1": 34.0, "size": 10.0,
            "color": (0.5, 0.0, 0.0), "family": "sans", "bold": True,
            "italic": False, "bold_prefix": False, "rotation": 90,
            "align": "left", "lines": 1, "in_table": False,
        },
    ]
    overlay = translate._make_layout_overlay(
        612.0, 792.0, blocks, ["Nota: Mantener seguro.", "CONFIDENCIAL"], dst_lang="es"
    )
    txt = overlay.extract_text() or ""
    assert "Nota:" in txt and "CONFIDENCIAL" in txt


def test_bilingual_mode_page_range_and_bookmarks(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    raw = tmp_path / "two_pages.pdf"
    c = canvas.Canvas(str(raw), pagesize=(612, 792))
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "First page heading.")
    c.showPage()
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Second page content.")
    c.showPage()
    c.save()

    # Add a bookmark to page 1
    src = tmp_path / "bookmarked.pdf"
    w = PdfWriter()
    r0 = PdfReader(str(raw))
    for p in r0.pages:
        w.add_page(p)
    w.add_outline_item(title="Introduction", page_number=0)
    with open(src, "wb") as fh:
        w.write(fh)

    # 1. Bilingual stacked mode with page range "1"
    out_bi = tmp_path / "bilingual.pdf"
    res_bi = translate.translate_pdf(
        src, out_bi, "en", "es",
        mode="bilingual",
        pages="1",
        translator=lambda batch: [f"ES:{s}" for s in batch],
    )
    assert res_bi["pages"] == 1
    bi_txt = (PdfReader(str(out_bi)).pages[0].extract_text() or "")
    assert "First page heading." in bi_txt and "ES:First page heading." in bi_txt
    assert "Second page" not in bi_txt

    # 2. In-place mode with page range "1" translates page 1 + bookmarks and keeps page 2 untouched
    out_ip = tmp_path / "inplace.pdf"
    res_ip = translate.translate_pdf(
        src, out_ip, "en", "es",
        mode="in-place",
        pages="1",
        translator=lambda batch: [f"ES:{s}" for s in batch],
    )
    assert res_ip["pages"] == 1
    assert res_ip["bookmarks_translated"] == 1
    r_ip = PdfReader(str(out_ip))
    assert len(r_ip.pages) == 2
    assert "ES:First page heading." in (r_ip.pages[0].extract_text() or "")
    assert "Second page content." in (r_ip.pages[1].extract_text() or "")
    assert "ES:" not in (r_ip.pages[1].extract_text() or "")
    assert r_ip.outline and r_ip.outline[0].title == "ES:Introduction"


def test_madlad_and_opus_mt_packs_and_notices(tmp_path, monkeypatch):
    from pdfmanager import notices

    monkeypatch.setattr(i18n, "packs_dir", lambda: tmp_path / "locales")
    cat = i18n.list_catalog_packs(fetch=False)
    by_code = {p["code"]: p for p in cat["packs"]}
    for code, min_size in (
        ("madlad-400-3b", 2_900_000_000),
        ("madlad-400-7b", 8_000_000_000),
        ("madlad-400-10b", 10_000_000_000),
    ):
        assert code in by_code
        assert by_code[code]["size_bytes"] >= min_size
        assert by_code[code]["license"] == "Apache-2.0"

    # Priority order: 10.7B > 7B > 3B
    for code in ("madlad-400-3b", "madlad-400-7b", "madlad-400-10b"):
        mdir = i18n.packs_dir() / code / "models" / code
        mdir.mkdir(parents=True)
        (mdir / "model.bin").write_bytes(b"bin")
        (mdir / "spiece.model").write_bytes(b"sp")
        assert translate.madlad_model_dir().name == code

    # Simulate installed OPUS-MT en-es + es-en pair bundle
    for pair in ("en-es", "es-en"):
        mdir = i18n.packs_dir() / "es" / "models" / pair
        mdir.mkdir(parents=True)
        (mdir / "model.bin").write_bytes(b"bin")
        (mdir / "source.spm").write_bytes(b"sp")
        (mdir / "target.spm").write_bytes(b"sp")
    cat2 = i18n.list_catalog_packs(fetch=False)
    by_code2 = {p["code"]: p for p in cat2["packs"]}
    assert by_code2["opus-es"]["installed"] is True
    assert translate.model_dir_for("en", "es") == i18n.packs_dir() / "es" / "models" / "en-es"

    # Third-party notices must attribute MADLAD-400, OPUS-MT, and Argos Translate
    text = notices.third_party_notices()
    assert "Google MADLAD-400 MT" in text
    assert "Helsinki-NLP OPUS-MT" in text
    assert "Argos Translate" in text







