# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Offline PDF translation engine and output modes (in-place, side-by-side, text).

Uses offline translation models from installed language packs
(``<data_dir>/locales/<code>/models/<src>-<dst>/`` via CTranslate2 or Argos
Translate when installed), or a caller-supplied ``translator`` function.
Numbers, reference codes (e.g. ``INV-2026-118``), URLs, and emails are
protected with placeholders so they pass through unchanged.
"""

import io
import re
from pathlib import Path

from pypdf import PageObject, PdfWriter, Transformation
from pypdf.generic import NameObject, TextStringObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from pdfmanager import core, fonts, i18n, progress

# Tokens preserved verbatim during translation (order matters: URLs/emails before codes/numbers).
_PROTECT_RE = re.compile(
    r"(?:"
    r"https?://[^\s<>()\"']+"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|\b[A-Z]{2,}(?:-[A-Z0-9]+){1,4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|(?:[$€£¥]\s?)?\b\d{1,3}(?:,\d{3})*(?:\.\d+)?%?\b"
    r")"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
_APPROX_PACK_MB = 45
MODES = ("side-by-side", "text", "in-place")


def protect_tokens(text):
    """Replace URLs, emails, reference codes, and numbers with stable placeholders.
    Returns ``(masked_text, tokens_list)``."""
    tokens = []

    def _repl(match):
        idx = len(tokens)
        tokens.append(match.group(0))
        return f"__ZTOK{idx}__"

    return _PROTECT_RE.sub(_repl, text), tokens


def restore_tokens(text, tokens):
    """Restore placeholders created by ``protect_tokens()``."""
    def _repl(match):
        idx = int(match.group(1))
        return tokens[idx] if 0 <= idx < len(tokens) else match.group(0)

    return re.sub(r"__ZTOK(\d+)__", _repl, text)


def split_sentences(text):
    """Split ``text`` into sentences while preserving order and non-empty segments."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    return [s for s in _SENTENCE_SPLIT_RE.split(cleaned) if s]


def _pair_candidates(src_lang, dst_lang):
    """Return candidate folder names for ``src_lang -> dst_lang`` (BCP-47 and Argos base codes)."""
    s = i18n.normalize_code(src_lang)
    d = i18n.normalize_code(dst_lang)
    s_arg = i18n.bcp47_to_argos(s)
    d_arg = i18n.bcp47_to_argos(d)
    cands = [f"{s}-{d}", f"{s_arg}-{d_arg}", f"{s}-{d_arg}", f"{s_arg}-{d}"]
    seen = []
    for c in cands:
        if c not in seen:
            seen.append(c)
    return seen


def installed_translation_pairs():
    """Return sorted list of installed offline translation model pair folder names."""
    root = i18n.packs_dir()
    if not root.is_dir():
        return []
    found = set()
    for pack_dir in root.iterdir():
        models_dir = pack_dir / "models"
        if pack_dir.is_dir() and models_dir.is_dir():
            for pair_dir in models_dir.iterdir():
                if pair_dir.is_dir():
                    found.add(pair_dir.name)
    return sorted(found)


def model_dir_for(src_lang, dst_lang):
    """Locate an installed offline translation model directory for ``src_lang -> dst_lang``."""
    s = i18n.normalize_code(src_lang)
    d = i18n.normalize_code(dst_lang)
    pair_names = _pair_candidates(s, d)
    root = i18n.packs_dir()
    # Check primary pack folders first (dst, src), then any other installed pack
    checked = set()
    for code in (d, s):
        checked.add(code)
        for pair in pair_names:
            cand = root / code / "models" / pair
            if cand.is_dir():
                return cand
    if root.is_dir():
        for pack_dir in sorted(root.iterdir()):
            if not pack_dir.is_dir() or pack_dir.name in checked:
                continue
            for pair in pair_names:
                cand = pack_dir / "models" / pair
                if cand.is_dir():
                    return cand
    return None


ensure_runtime = i18n.ensure_translation_runtime


class _SubwordBpe:
    """Minimal subword-nmt BPE encoder/decoder for Argos 1.9 ``bpe.model`` packs."""

    _BPE_PUNCT_RE = re.compile(r"([.,!?;:()\[\]\"'])")

    def __init__(self, model_path):
        ranks = {}
        with open(model_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    pair = (parts[0], parts[1])
                    if pair not in ranks:
                        ranks[pair] = len(ranks)
        self._ranks = ranks
        self._cache = {}

    def _encode_word(self, word):
        cached = self._cache.get(word)
        if cached is not None:
            return cached
        if len(word) == 1:
            res = [word]
            self._cache[word] = res
            return res
        symbols = list(word[:-1]) + [word[-1] + "</w>"]
        ranks = self._ranks
        while len(symbols) > 1:
            best_pair = None
            best_rank = None
            for i in range(len(symbols) - 1):
                pair = (symbols[i], symbols[i + 1])
                rank = ranks.get(pair)
                if rank is not None and (best_rank is None or rank < best_rank):
                    best_rank = rank
                    best_pair = pair
            if best_pair is None:
                break
            a, b = best_pair
            merged = []
            i = 0
            while i < len(symbols):
                if i < len(symbols) - 1 and symbols[i] == a and symbols[i + 1] == b:
                    merged.append(a + b)
                    i += 2
                else:
                    merged.append(symbols[i])
                    i += 1
            symbols = merged
        out = []
        for idx, sym in enumerate(symbols):
            if idx < len(symbols) - 1:
                out.append(sym + "@@")
            else:
                out.append(sym[:-4] if sym.endswith("</w>") else sym)
        self._cache[word] = out
        return out

    def encode(self, text, out_type=str):  # noqa: ARG002 - mirrors SentencePieceProcessor
        spaced = self._BPE_PUNCT_RE.sub(r" \1 ", text)
        tokens = []
        for word in spaced.split():
            tokens.extend(self._encode_word(word))
        return tokens

    def decode(self, tokens):
        text = " ".join(tokens).replace("@@ ", "").replace("@@", "")
        text = text.replace(" @-@ ", "-").replace("&quot;", '"').replace("&apos;", "'")
        text = re.sub(r"\s+([.,!?;:)])", r"\1", text)
        text = re.sub(r"([(])\s+", r"\1", text)
        return text.strip()


def _load_tokenizer(model_path, spm):
    """Load a SentencePiece ``.model`` or subword-nmt ``bpe.model`` tokenizer."""
    with open(model_path, "rb") as fh:
        head = fh.read(32)
    if head.startswith(b"#version:"):
        return _SubwordBpe(model_path)
    return spm.SentencePieceProcessor(model_file=str(model_path))


def _load_single_translator(s, d, mdir):
    """Load a single-hop offline translator from ``mdir`` (CTranslate2 or Argos Translate)."""
    ct2_loaded = False
    for attempt in range(2):
        try:
            import ctranslate2
            import sentencepiece as spm
            ct2_loaded = True
            break
        except ImportError:
            if attempt == 0 and ensure_runtime():
                continue
            break

    if ct2_loaded:
        # Support both flat layout (mdir/model.bin) and nested Argos layout (mdir/model/model.bin)
        ct2_dir = mdir / "model" if (mdir / "model").is_dir() and not (mdir / "model.bin").is_file() else mdir
        sp_src_file = next(
            (mdir / name for name in ("sentencepiece_src.model", "source.spm") if (mdir / name).is_file()),
            None,
        )
        sp_tgt_file = next(
            (mdir / name for name in ("sentencepiece_tgt.model", "target.spm") if (mdir / name).is_file()),
            None,
        )
        if sp_src_file and sp_tgt_file:
            sp_src = _load_tokenizer(sp_src_file, spm)
            sp_tgt = _load_tokenizer(sp_tgt_file, spm)
        else:
            sp_files = sorted(mdir.glob("*.model")) or sorted(ct2_dir.glob("*.model"))
            if not sp_files:
                raise core.PdfManagerError(
                    f"found model folder for {s} → {d} at {mdir}, but no tokenizer (.model) file was found"
                )
            sp_src = sp_tgt = _load_tokenizer(sp_files[0], spm)

        tr = ctranslate2.Translator(str(ct2_dir), device="cpu")

        def _ct2_batch(batch):
            tokenized = [sp_src.encode(text, out_type=str) for text in batch]
            results = tr.translate_batch(
                tokenized,
                beam_size=4,
                replace_unknowns=True,
                no_repeat_ngram_size=4,
                max_decoding_length=512,
            )
            return [_clean_mt_output(sp_tgt.decode(r.hypotheses[0]), d) for r in results]

        _ct2_batch._neural = True
        return _ct2_batch

    # Fallback: Argos Translate if installed
    try:
        import argostranslate.translate as argos

        s_base, d_base = i18n.bcp47_to_argos(s), i18n.bcp47_to_argos(d)
        installed = argos.get_installed_languages()
        from_lang = next((l for l in installed if l.code == s_base), None)
        to_lang = next((l for l in installed if l.code == d_base), None)
        if from_lang and to_lang:
            tr = from_lang.get_translation(to_lang)
            if tr is not None:
                def _argos_batch(batch):
                    return [_clean_mt_output(tr.translate(text), d) for text in batch]

                _argos_batch._neural = True
                return _argos_batch
    except ImportError:
        pass

    raise core.PdfManagerError(
        f"found model folder for {s} → {d} at {mdir}, but no offline translation runtime "
        "(ctranslate2 + sentencepiece, or argostranslate) is installed. "
        "Update ZPDF, re-run Install ZPDF.cmd, or run: pip install ctranslate2 sentencepiece"
    )


_CJK_LANGS = {"zh", "zh-Hans", "zh-Hant", "ja", "ko"}
_CJK_CHAR_RE = re.compile(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af]")
_CJK_SPACE_RE = re.compile(
    r"(?<=[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af])\s+"
    r"(?=[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af])"
)
_BULLET_LINE_RE = re.compile(r"^(?:[\u2022\u2023\u25e6\u2043\u2219•▪▸►\-*–—]|\d+[.)]|[A-Za-z][.)])\s+")


def _is_cjk_lang(lang):
    norm = i18n.normalize_code(lang or "")
    return norm in _CJK_LANGS or norm.split("-", 1)[0] in _CJK_LANGS


def _clean_mt_output(text, dst_lang):
    """Strip leaked SentencePiece ``▁`` markers and normalize whitespace."""
    out = str(text or "").replace("\u2581", " ")
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    if _is_cjk_lang(dst_lang):
        out = _CJK_SPACE_RE.sub("", out)
    return out


def _extract_paragraphs(raw_text):
    """Reflow ``pypdf.extract_text()`` lines into clean paragraphs & standalone headings.

    PDF text extraction emits a single ``\\n`` at every visual line wrap. Joining
    continuing prose lines while keeping headings, bullets, and blank-line breaks
    separate prevents neural MT models from dropping headings or mangling
    mid-sentence wraps.
    """
    lines = [ln.rstrip() for ln in str(raw_text or "").splitlines()]
    paragraphs = []
    buf = []

    def _flush():
        if buf:
            text = " ".join(buf).strip()
            text = re.sub(r"[ \t]{2,}", " ", text)
            if text:
                paragraphs.append(text)
            buf.clear()

    for raw_ln in lines:
        ln = raw_ln.strip()
        if not ln:
            _flush()
            continue
        if _BULLET_LINE_RE.match(ln):
            _flush()
            buf.append(ln)
            continue
        if not buf:
            buf.append(ln)
            continue
        prev = buf[-1]
        # De-hyphenate wrapped English/Latin words ("agree-\nment" -> "agreement")
        if prev.endswith("-") and len(prev) >= 2 and prev[-2].isalpha() and ln[0].islower():
            buf[-1] = prev[:-1] + ln
            continue
        # Keep short headings/labels or lines ending in sentence punctuation / colon separate
        prev_ends_block = prev[-1] in ".!?。！？:" or len(prev) < 42
        if prev_ends_block and (ln[0].isupper() or _CJK_CHAR_RE.match(ln[0]) or ln[0].isdigit()):
            _flush()
            buf.append(ln)
        else:
            buf.append(ln)
    _flush()
    return paragraphs


def _load_offline_translator(src_lang, dst_lang):
    """Return a batch callable ``fn(list[str]) -> list[str]`` backed by an installed
    language pack model (CTranslate2 or Argos Translate), automatically pivoting
    through English when direct ``src -> dst`` is absent, or raise PdfManagerError."""
    s = i18n.normalize_code(src_lang)
    d = i18n.normalize_code(dst_lang)
    if s == d:
        return lambda batch: list(batch)

    mdir = model_dir_for(s, d)
    if mdir is not None:
        return _load_single_translator(s, d, mdir)

    # Automatic English pivot: src -> en -> dst
    if s != i18n.DEFAULT_LANG and d != i18n.DEFAULT_LANG:
        hop1 = model_dir_for(s, i18n.DEFAULT_LANG)
        hop2 = model_dir_for(i18n.DEFAULT_LANG, d)
        if hop1 is not None and hop2 is not None:
            fn1 = _load_single_translator(s, i18n.DEFAULT_LANG, hop1)
            fn2 = _load_single_translator(i18n.DEFAULT_LANG, d, hop2)

            def _pivot_batch(batch):
                return fn2(fn1(batch))

            _pivot_batch._neural = bool(getattr(fn1, "_neural", False) or getattr(fn2, "_neural", False))
            return _pivot_batch

    pack_code = d if d != i18n.DEFAULT_LANG else s
    raise core.PdfManagerError(
        f"translation from {s} to {d} requires the {pack_code!r} language pack "
        f"(~{_APPROX_PACK_MB} MB); download it now to continue"
    )


def translate_texts(texts, src_lang, dst_lang, *, translator=None, batch_size=32):
    """Translate a sequence of strings offline, preserving placeholders (for
    non-neural translators) and deduplicating identical strings so each unique
    sentence is translated once."""
    fn = translator if translator is not None else _load_offline_translator(src_lang, dst_lang)
    use_placeholders = not getattr(fn, "_neural", False)
    joiner = "" if _is_cjk_lang(dst_lang) else " "
    cache = {}
    unique_sentences = []
    per_text_plan = []

    for raw in texts:
        if not raw or not str(raw).strip():
            per_text_plan.append(None)
            continue
        text_str = str(raw)
        if use_placeholders:
            masked, tokens = protect_tokens(text_str)
        else:
            masked, tokens = text_str, ()
        sents = split_sentences(masked)
        per_text_plan.append((sents, tokens))
        for s in sents:
            if s not in cache:
                cache[s] = None
                unique_sentences.append(s)

    total = len(unique_sentences)
    for start in range(0, total, max(1, int(batch_size))):
        progress.check()
        progress.report(start, max(1, total), "translating text")
        chunk = unique_sentences[start : start + batch_size]
        translated_chunk = fn(chunk)
        if len(translated_chunk) != len(chunk):
            raise core.PdfManagerError("translation engine returned mismatched batch size")
        for orig, out in zip(chunk, translated_chunk):
            cache[orig] = out

    results = []
    for item in per_text_plan:
        if item is None:
            results.append("")
            continue
        sents, tokens = item
        joined = joiner.join(cache.get(s, s) for s in sents)
        results.append(restore_tokens(joined, tokens) if tokens else joined)
    return results


def translate_page(path, page_num, src_lang, dst_lang, *, password=None, translator=None):
    """Translate a single 1-based page of ``path`` without modifying the PDF."""
    reader = core.open_pdf(Path(path), password=password)
    total = len(reader.pages)
    if page_num < 1 or page_num > total:
        raise core.PdfManagerError(f"page {page_num} is out of range (1–{total})")
    raw_text = (reader.pages[page_num - 1].extract_text() or "").strip()
    paragraphs = _extract_paragraphs(raw_text)
    if not paragraphs:
        return {"page": page_num, "src_lang": src_lang, "dst_lang": dst_lang, "original": "", "translated": ""}
    translated_paras = translate_texts(paragraphs, src_lang, dst_lang, translator=translator)
    return {
        "page": page_num,
        "src_lang": i18n.normalize_code(src_lang),
        "dst_lang": i18n.normalize_code(dst_lang),
        "original": "\n\n".join(paragraphs),
        "translated": "\n\n".join(translated_paras),
    }


def _wrap_text(text, font_name, font_size, max_w):
    """Wrap ``text`` to ``max_w`` points, handling both space-delimited Latin
    text and spaceless CJK scripts (Chinese, Japanese, Korean)."""
    if not text:
        return [""]
    lines = []
    # Tokenize into CJK single chars, whitespace, or non-CJK words
    parts = re.findall(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af]|\s+|[^\s\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af]+", text)
    cur = ""
    for part in parts:
        if not cur and part.isspace():
            continue
        cand = cur + part
        if pdfmetrics.stringWidth(cand, font_name, font_size) <= max_w:
            cur = cand
            continue
        if cur.strip():
            lines.append(cur.rstrip())
            cur = ""
        if part.isspace():
            continue
        if pdfmetrics.stringWidth(part, font_name, font_size) <= max_w:
            cur = part
        else:
            # Break an overlong token (e.g. URL) by character
            chunk = ""
            for ch in part:
                if chunk and pdfmetrics.stringWidth(chunk + ch, font_name, font_size) > max_w:
                    lines.append(chunk)
                    chunk = ch
                else:
                    chunk += ch
            cur = chunk
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _make_text_page(width, height, text, font_name=None, font_size=10):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    margin = 36
    max_w = max(72, width - 2 * margin)
    avail_h = max(72, height - 2 * margin)
    chosen_font = font_name or fonts.font_for(text or "")
    paras = (text or "").split("\n\n")

    # Auto-fit font size down gently if a dense translated page would overflow
    size = float(font_size)
    wrapped = []
    for candidate_size in (size, min(size, 9.0), min(size, 8.0), min(size, 7.0)):
        leading = candidate_size * 1.4
        wrapped = [_wrap_text(p, chosen_font, candidate_size, max_w) for p in paras]
        needed_h = sum(len(lines) * leading + leading * 0.45 for lines in wrapped)
        size = candidate_size
        if needed_h <= avail_h:
            break

    y = height - margin
    leading = size * 1.4
    c.setFont(chosen_font, size)
    for lines in wrapped:
        for line in lines:
            if y < margin:
                break
            c.drawString(margin, y, line)
            y -= leading
        y -= leading * 0.45
    c.showPage()
    c.save()
    buf.seek(0)
    from pypdf import PdfReader
    return PdfReader(buf).pages[0]


def _translate_annotations_and_fields(writer, src_lang, dst_lang, translator):
    """Translate annotation /Contents and text form field values in-place on ``writer``."""
    targets = []
    for page in writer.pages:
        annots = page.get("/Annots")
        if hasattr(annots, "get_object"):
            annots = annots.get_object()
        for ref in annots or ():
            annot = ref.get_object() if hasattr(ref, "get_object") else ref
            if not hasattr(annot, "get"):
                continue
            subtype = str(annot.get("/Subtype", ""))
            if subtype == "/Widget":
                ft = str(annot.get("/FT", ""))
                val = annot.get("/V")
                if ft == "/Tx" and val and str(val).strip():
                    targets.append((annot, NameObject("/V"), str(val)))
            else:
                contents = annot.get("/Contents")
                if contents and str(contents).strip():
                    targets.append((annot, NameObject("/Contents"), str(contents)))
    if not targets:
        return 0
    translated = translate_texts([t[2] for t in targets], src_lang, dst_lang, translator=translator)
    for (obj, key, _), new_val in zip(targets, translated):
        obj[key] = TextStringObject(new_val)
    return len(targets)


def translate_pdf(
    input_path,
    output_path,
    src_lang,
    dst_lang,
    *,
    mode="side-by-side",
    password=None,
    translator=None,
):
    """Translate a PDF into one of three output modes:
    - ``side-by-side``: double-width PDF with original page left, translation right.
    - ``text``: plain-text ``.txt`` file with page markers.
    - ``in-place``: writes translated page overlay + translated annotations & form fields.
    """
    src = Path(input_path)
    dst = Path(output_path)
    mode = (mode or "side-by-side").strip().lower()
    if mode not in ("side-by-side", "text", "in-place"):
        raise core.PdfManagerError(f"unknown translation mode: {mode!r}")

    reader = core.open_pdf(src, password=password)
    pages_raw = []
    for page in reader.pages:
        txt = (page.extract_text() or "").strip()
        pages_raw.append(_extract_paragraphs(txt))

    flat_paras = [p for paras in pages_raw for p in paras]
    flat_translated = translate_texts(flat_paras, src_lang, dst_lang, translator=translator)

    pages_translated = []
    cursor = 0
    for paras in pages_raw:
        chunk = flat_translated[cursor : cursor + len(paras)]
        cursor += len(paras)
        pages_translated.append("\n\n".join(chunk))

    if mode == "text":
        sections = [f"--- Page {i} ---\n{txt}" for i, txt in enumerate(pages_translated, start=1)]
        dst.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
        return {"mode": mode, "pages": len(pages_translated), "output": str(dst)}

    writer = PdfWriter()
    if mode == "side-by-side":
        for orig_page, trans_text in zip(reader.pages, pages_translated):
            w = float(orig_page.mediabox.width)
            h = float(orig_page.mediabox.height)
            spread = PageObject.create_blank_page(width=w * 2, height=h)
            spread.merge_page(orig_page)
            right_page = _make_text_page(w, h, trans_text or "(no text on page)")
            spread.merge_transformed_page(right_page, Transformation().translate(w, 0))
            writer.add_page(spread)
        with open(dst, "wb") as fh:
            writer.write(fh)
        return {"mode": mode, "pages": len(pages_translated), "output": str(dst)}

    # in-place mode: replace page text cleanly + translate annotations & form values
    for orig_page, trans_text in zip(reader.pages, pages_translated):
        w = float(orig_page.mediabox.width)
        h = float(orig_page.mediabox.height)
        new_page = _make_text_page(w, h, trans_text)
        # Carry over annotations/widgets so they can be translated in place
        if "/Annots" in orig_page:
            new_page[NameObject("/Annots")] = orig_page["/Annots"]
        writer.add_page(new_page)
    updated_items = _translate_annotations_and_fields(writer, src_lang, dst_lang, translator)
    with open(dst, "wb") as fh:
        writer.write(fh)
    return {"mode": mode, "pages": len(pages_translated), "annotations_translated": updated_items, "output": str(dst)}
