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


# Entities protected during neural translation using collision-free numeric sentinels
# (URLs, emails, phone numbers, reference codes, ISO dates, currency & formatted numbers).
# Plain small integers / years (e.g. "December 31, 2026", "3 items") are left unmasked so
# the neural model translates dates and counts into natural target-language grammar.
_NEURAL_PROTECT_RE = re.compile(
    r"(?:"
    r"https?://[^\s<>()\"']+[^\s<>()\"'.,;:!?]"
    r"|www\.[^\s<>()\"']+[^\s<>()\"'.,;:!?]"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}"
    r"|\b[A-Z]{2,}(?:-[A-Z0-9]+){1,4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|(?:[$€£¥]\s?\d[\d,]*(?:\.\d+)?)"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"
    r")"
)
_PROSE_WORD_RE = re.compile(r"[A-Za-z\u00c0-\u024f\u0400-\u04ff\u0600-\u06ff]{2,}|[\u3040-\u9fff\uac00-\ud7af]")


def _is_verbatim_block(text):
    """Return True when ``text`` contains only non-translatable data (numbers,
    currency, dates, emails, URLs, phone numbers, codes, punctuation)."""
    stripped = _NEURAL_PROTECT_RE.sub(" ", text)
    return _PROSE_WORD_RE.search(stripped) is None


def _protect_neural_entities(text):
    """Replace emails, URLs, phone numbers, codes, ISO dates, and currency with
    collision-free 6-digit numeric sentinels that neural MT preserves verbatim."""
    mapping = []
    next_id = 910001

    def _repl(match):
        nonlocal next_id
        while str(next_id) in text:
            next_id += 1
        sentinel = str(next_id)
        next_id += 1
        mapping.append((sentinel, match.group(0)))
        return sentinel

    return _NEURAL_PROTECT_RE.sub(_repl, text), mapping


def _restore_neural_entities(translated, mapping):
    """Restore numeric sentinels in ``translated``; returns ``(text, all_found)``."""
    out = translated
    all_found = True
    for sentinel, original in mapping:
        pattern = re.compile(r"\s*".join(re.escape(ch) for ch in sentinel))
        m = pattern.search(out)
        if not m:
            all_found = False
            continue
        before = out[: m.start()]
        after = out[m.end() :]
        pad_l = " " if before and before[-1].isdigit() and original[0].isdigit() else ""
        pad_r = " " if after and after[0].isdigit() and original[-1].isdigit() else ""
        out = before + pad_l + original + pad_r + after
    return out, all_found


def _translate_around_entities(text, fn, joiner):
    """Fallback when a neural model drops a sentinel: translate only the prose
    segments between protected entities so every entity stays 100% verbatim."""
    pieces = []
    prose_indices = []
    prose_texts = []
    pos = 0
    for m in _NEURAL_PROTECT_RE.finditer(text):
        before = text[pos : m.start()]
        if before.strip():
            prose_indices.append(len(pieces))
            prose_texts.append(before.strip())
            pieces.append("")
        elif before:
            pieces.append(before)
        pieces.append(m.group(0))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        prose_indices.append(len(pieces))
        prose_texts.append(tail.strip())
        pieces.append("")
    elif tail:
        pieces.append(tail)
    if prose_texts:
        translated = fn(prose_texts)
        for idx, tr in zip(prose_indices, translated):
            pieces[idx] = tr
    out = joiner.join(p for p in pieces if p)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def translate_texts(texts, src_lang, dst_lang, *, translator=None, batch_size=32):
    """Translate a sequence of strings offline, preserving emails, URLs, phone
    numbers, reference codes, dates, and currency, and deduplicating identical
    sentences so each unique sentence is translated once."""
    fn = translator if translator is not None else _load_offline_translator(src_lang, dst_lang)
    is_neural = bool(getattr(fn, "_neural", False))
    joiner = "" if _is_cjk_lang(dst_lang) else " "
    cache = {}
    unique_sentences = []
    per_text_plan = []

    for raw in texts:
        if not raw or not str(raw).strip():
            per_text_plan.append(None)
            continue
        text_str = str(raw)
        if is_neural and _is_verbatim_block(text_str):
            per_text_plan.append(("verbatim", text_str))
            continue
        if is_neural:
            masked, mapping = _protect_neural_entities(text_str)
            sents = split_sentences(masked)
            per_text_plan.append(("neural", text_str, sents, mapping))
        else:
            masked, tokens = protect_tokens(text_str)
            sents = split_sentences(masked)
            per_text_plan.append(("tokens", sents, tokens))
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
        kind = item[0]
        if kind == "verbatim":
            results.append(item[1])
        elif kind == "neural":
            _, orig_text, sents, mapping = item
            joined = joiner.join(cache.get(s, s) for s in sents)
            if mapping:
                restored, ok = _restore_neural_entities(joined, mapping)
                if not ok:
                    restored = _translate_around_entities(orig_text, fn, joiner)
                results.append(restored)
            else:
                results.append(joined)
        else:
            _, sents, tokens = item
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


def _normalize_color(raw):
    """Convert a pdfplumber ``non_stroking_color`` value to an ``(r, g, b)`` 0..1 tuple."""
    if raw is None:
        return (0.0, 0.0, 0.0)
    if isinstance(raw, (int, float)):
        g = max(0.0, min(1.0, float(raw)))
        return (g, g, g)
    if isinstance(raw, (list, tuple)):
        if len(raw) == 1:
            g = max(0.0, min(1.0, float(raw[0])))
            return (g, g, g)
        if len(raw) == 3:
            return tuple(max(0.0, min(1.0, float(v))) for v in raw)
        if len(raw) == 4:
            c, m, y, k = (max(0.0, min(1.0, float(v))) for v in raw)
            return ((1.0 - c) * (1.0 - k), (1.0 - m) * (1.0 - k), (1.0 - y) * (1.0 - k))
    return (0.0, 0.0, 0.0)


def _luminance(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _sample_bg_rgb(bg_image, x0, top, x1, bottom, page_w, page_h):
    """Sample median background RGB around a block's border from a rendered page image."""
    if bg_image is None or page_w <= 0 or page_h <= 0:
        return (1.0, 1.0, 1.0)
    iw, ih = bg_image.size
    sx, sy = iw / page_w, ih / page_h
    px0 = max(0, min(iw - 1, int(x0 * sx) - 2))
    px1 = max(0, min(iw - 1, int(x1 * sx) + 2))
    py0 = max(0, min(ih - 1, int(top * sy) - 2))
    py1 = max(0, min(ih - 1, int(bottom * sy) + 2))
    samples = []
    step_x = max(1, (px1 - px0) // 12)
    step_y = max(1, (py1 - py0) // 6)
    for x in range(px0, px1 + 1, step_x):
        samples.append(bg_image.getpixel((x, py0)))
        samples.append(bg_image.getpixel((x, py1)))
    for y in range(py0, py1 + 1, step_y):
        samples.append(bg_image.getpixel((px0, y)))
        samples.append(bg_image.getpixel((px1, y)))
    if not samples:
        return (1.0, 1.0, 1.0)
    rs = sorted(p[0] for p in samples)
    gs = sorted(p[1] for p in samples)
    bs = sorted(p[2] for p in samples)
    mid = len(samples) // 2
    return (rs[mid] / 255.0, gs[mid] / 255.0, bs[mid] / 255.0)


def _extract_layout_blocks(plumber_page):
    """Extract positioned text blocks ``{text, x0, top, x1, bottom, size, color, lines}``
    from a ``pdfplumber`` page, preserving columns, table cells, headings, and font sizes."""
    words = plumber_page.extract_words(
        extra_attrs=["size", "non_stroking_color"],
        keep_blank_chars=False,
        use_text_flow=False,
    )
    if not words:
        return []

    # 1. Group words into visual lines by vertical top coordinate
    words.sort(key=lambda w: (round(float(w["top"]) / 2.5), float(w["x0"])))
    raw_lines = []
    for w in words:
        w_top = float(w["top"])
        w_size = max(4.0, float(w.get("size") or 10.0))
        if raw_lines and abs(w_top - raw_lines[-1]["top"]) <= max(3.0, raw_lines[-1]["size"] * 0.45):
            raw_lines[-1]["words"].append(w)
        else:
            raw_lines.append({"top": w_top, "size": w_size, "words": [w]})

    # 2. Split each visual line on large horizontal gaps (separates columns & table cells)
    segments = []
    for ln in raw_lines:
        lwords = sorted(ln["words"], key=lambda w: float(w["x0"]))
        cur = [lwords[0]]
        for w in lwords[1:]:
            prev = cur[-1]
            prev_size = max(4.0, float(prev.get("size") or 10.0))
            gap = float(w["x0"]) - float(prev["x1"])
            if gap > max(18.0, prev_size * 1.8):
                segments.append(cur)
                cur = [w]
            else:
                cur.append(w)
        segments.append(cur)

    seg_objs = []
    for seg in segments:
        sizes = [max(4.0, float(w.get("size") or 10.0)) for w in seg]
        avg_size = sum(sizes) / len(sizes)
        seg_objs.append({
            "text": " ".join(w["text"] for w in seg),
            "x0": min(float(w["x0"]) for w in seg),
            "top": min(float(w["top"]) for w in seg),
            "x1": max(float(w["x1"]) for w in seg),
            "bottom": max(float(w["bottom"]) for w in seg),
            "size": avg_size,
            "color": _normalize_color(seg[0].get("non_stroking_color")),
            "lines": 1,
        })

    # 3. Merge vertically adjacent line segments in the same column & font size into blocks
    seg_objs.sort(key=lambda s: (s["top"], s["x0"]))
    blocks = []
    for seg in seg_objs:
        merged = False
        if not _BULLET_LINE_RE.match(seg["text"]):
            for blk in reversed(blocks):
                v_gap = seg["top"] - blk["bottom"]
                if v_gap > max(7.0, blk["size"] * 0.85):
                    continue
                if v_gap < -blk["size"] * 0.35:
                    continue
                same_size = abs(seg["size"] - blk["size"]) <= max(1.0, blk["size"] * 0.15)
                same_left = abs(seg["x0"] - blk["x0"]) <= max(18.0, blk["size"] * 2.0)
                horiz_overlap = min(seg["x1"], blk["x1"]) > max(seg["x0"], blk["x0"])
                blk_w = blk["x1"] - blk["x0"]
                page_w = float(plumber_page.width or 612.0)
                short_heading = blk["lines"] == 1 and blk_w < min(140.0, page_w * 0.28) and blk["text"][-1] not in ",;"
                if same_size and same_left and horiz_overlap and not short_heading:
                    prev_text = blk["text"]
                    nxt_text = seg["text"]
                    if prev_text.endswith("-") and len(prev_text) >= 2 and prev_text[-2].isalpha() and nxt_text[0].islower():
                        blk["text"] = prev_text[:-1] + nxt_text
                    else:
                        blk["text"] = prev_text + " " + nxt_text
                    blk["x0"] = min(blk["x0"], seg["x0"])
                    blk["x1"] = max(blk["x1"], seg["x1"])
                    blk["bottom"] = max(blk["bottom"], seg["bottom"])
                    blk["lines"] += 1
                    merged = True
                    break
        if not merged:
            blocks.append(seg)
    return blocks


def _make_layout_overlay(width, height, blocks, translated_texts, bg_image=None):
    """Build a transparent PDF overlay page that covers each original text block
    with its sampled background color and draws the translated text in-place
    with original font size, color, and CJK-aware auto-fit wrapping."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))

    for idx, (blk, trans) in enumerate(zip(blocks, translated_texts)):
        text = (trans or "").strip()
        if not text:
            continue
        x0, top, x1, bottom = blk["x0"], blk["top"], blk["x1"], blk["bottom"]
        orig_size = max(5.5, float(blk["size"]))
        pad = min(2.0, orig_size * 0.18)

        # Cover original text with sampled background color
        bg_rgb = _sample_bg_rgb(bg_image, x0, top, x1, bottom, width, height)
        rect_x = max(0.0, x0 - pad)
        rect_y = max(0.0, height - bottom - pad)
        rect_w = min(width - rect_x, (x1 - x0) + 2 * pad)
        rect_h = min(height - rect_y, (bottom - top) + 2 * pad)
        c.setFillColorRGB(*bg_rgb)
        c.rect(rect_x, rect_y, rect_w, rect_h, stroke=0, fill=1)

        # Allow single-line labels/headings to expand into free space on their right
        avail_w = max(18.0, x1 - x0)
        if blk["lines"] == 1:
            right_limit = width - 18.0
            for j, other in enumerate(blocks):
                if j == idx:
                    continue
                if other["top"] < bottom and other["bottom"] > top and other["x0"] >= x1 - 2.0:
                    right_limit = min(right_limit, other["x0"] - 6.0)
            avail_w = max(avail_w, right_limit - x0)

        avail_h = max(orig_size * 1.25, (bottom - top) + 2 * pad)
        chosen_font = fonts.font_for(text)

        # Auto-fit font size down gently until wrapped lines fit inside the block
        min_size = max(5.5, orig_size * 0.55)
        fit_size = orig_size
        wrapped = _wrap_text(text, chosen_font, fit_size, avail_w)
        while len(wrapped) * (fit_size * 1.2) > avail_h + 1.0 and fit_size > min_size:
            fit_size = max(min_size, fit_size * 0.9)
            wrapped = _wrap_text(text, chosen_font, fit_size, avail_w)

        # Ensure readable contrast between text color and sampled background
        fg_rgb = blk["color"]
        if abs(_luminance(fg_rgb) - _luminance(bg_rgb)) < 0.25:
            fg_rgb = (0.0, 0.0, 0.0) if _luminance(bg_rgb) > 0.5 else (1.0, 1.0, 1.0)

        c.setFillColorRGB(*fg_rgb)
        c.setFont(chosen_font, fit_size)
        leading = fit_size * 1.2
        y = height - top - fit_size * 0.88
        for line in wrapped:
            if y < max(4.0, height - bottom - pad - leading * 0.35):
                break
            c.drawString(x0, y, line)
            y -= leading

    c.showPage()
    c.save()
    buf.seek(0)
    from pypdf import PdfReader
    return PdfReader(buf).pages[0]


def _render_page_bg(pdfium_doc, page_index):
    """Render a 72-dpi RGB PIL image for background-color sampling (or None)."""
    if pdfium_doc is None:
        return None
    try:
        with core.PDFIUM_LOCK:
            return pdfium_doc[page_index].render(scale=1.0).to_pil().convert("RGB")
    except Exception:  # noqa: BLE001
        return None


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
    - ``side-by-side``: double-width PDF with original page left, layout-preserved translation right.
    - ``text``: plain-text ``.txt`` file with page markers.
    - ``in-place``: layout-preserving translation overlaid on the original pages + translated annotations & form fields.
    """
    src = Path(input_path)
    dst = Path(output_path)
    mode = (mode or "side-by-side").strip().lower()
    if mode not in ("side-by-side", "text", "in-place"):
        raise core.PdfManagerError(f"unknown translation mode: {mode!r}")

    reader = core.open_pdf(src, password=password)

    if mode == "text":
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
        sections = [f"--- Page {i} ---\n{txt}" for i, txt in enumerate(pages_translated, start=1)]
        dst.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
        return {"mode": mode, "pages": len(pages_translated), "output": str(dst)}

    # Extract positioned layout blocks per page via pdfplumber so images, tables,
    # borders, columns, font sizes, and colors stay intact.
    pages_blocks = []
    try:
        import pdfplumber

        with pdfplumber.open(str(src), password=password or "") as pdoc:
            for ppage in pdoc.pages:
                pages_blocks.append(_extract_layout_blocks(ppage))
    except Exception:  # noqa: BLE001 - fall back to plain text pages if pdfplumber fails
        pages_blocks = [[] for _ in reader.pages]

    if len(pages_blocks) != len(reader.pages):
        pages_blocks = [[] for _ in reader.pages]

    # Translate all positioned blocks in one deduplicated batch
    flat_texts = [blk["text"] for blks in pages_blocks for blk in blks]
    flat_translated = translate_texts(flat_texts, src_lang, dst_lang, translator=translator) if flat_texts else []

    pages_trans_blocks = []
    cursor = 0
    for blks in pages_blocks:
        chunk = flat_translated[cursor : cursor + len(blks)]
        cursor += len(blks)
        pages_trans_blocks.append(chunk)

    # Optional 72-dpi render for exact background-color matching under each text block
    pdfium_doc = None
    try:
        import pypdfium2 as pdfium

        with core.PDFIUM_LOCK:
            pdfium_doc = pdfium.PdfDocument(str(src), password=password)
    except Exception:  # noqa: BLE001
        pdfium_doc = None

    try:
        writer = PdfWriter()
        if mode == "side-by-side":
            for idx, (orig_page, blks, trans_blks) in enumerate(
                zip(reader.pages, pages_blocks, pages_trans_blocks)
            ):
                w = float(orig_page.mediabox.width)
                h = float(orig_page.mediabox.height)
                spread = writer.add_blank_page(width=w * 2, height=h)
                spread.merge_page(orig_page)
                if blks:
                    bg_img = _render_page_bg(pdfium_doc, idx)
                    overlay = _make_layout_overlay(w, h, blks, trans_blks, bg_image=bg_img)
                    shift = Transformation().translate(w, 0)
                    spread.merge_transformed_page(orig_page, shift)
                    spread.merge_transformed_page(overlay, shift)
                else:
                    raw_txt = (orig_page.extract_text() or "").strip()
                    fallback = "\n\n".join(
                        translate_texts(_extract_paragraphs(raw_txt), src_lang, dst_lang, translator=translator)
                    ) if raw_txt else "(no text on page)"
                    right_page = _make_text_page(w, h, fallback)
                    spread.merge_transformed_page(right_page, Transformation().translate(w, 0))
            with open(dst, "wb") as fh:
                writer.write(fh)
            return {"mode": mode, "pages": len(reader.pages), "output": str(dst)}

        # in-place mode: overlay translated blocks directly onto the original pages
        # (preserving images, tables, borders, colors, and layout) + translate annotations/fields
        for idx, (orig_page, blks, trans_blks) in enumerate(zip(reader.pages, pages_blocks, pages_trans_blocks)):
            w = float(orig_page.mediabox.width)
            h = float(orig_page.mediabox.height)
            if blks:
                bg_img = _render_page_bg(pdfium_doc, idx)
                overlay = _make_layout_overlay(w, h, blks, trans_blks, bg_image=bg_img)
                attached = writer.add_page(orig_page)
                attached.merge_page(overlay)
            else:
                raw_txt = (orig_page.extract_text() or "").strip()
                fallback = "\n\n".join(
                    translate_texts(_extract_paragraphs(raw_txt), src_lang, dst_lang, translator=translator)
                ) if raw_txt else ""
                new_page = _make_text_page(w, h, fallback)
                if "/Annots" in orig_page:
                    new_page[NameObject("/Annots")] = orig_page["/Annots"]
                writer.add_page(new_page)
        updated_items = _translate_annotations_and_fields(writer, src_lang, dst_lang, translator)
        with open(dst, "wb") as fh:
            writer.write(fh)
        return {"mode": mode, "pages": len(reader.pages), "annotations_translated": updated_items, "output": str(dst)}
    finally:
        if pdfium_doc is not None:
            try:
                with core.PDFIUM_LOCK:
                    pdfium_doc.close()
            except Exception:  # noqa: BLE001
                pass
