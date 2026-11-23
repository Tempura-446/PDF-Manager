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


_ABBREV_END_RE = re.compile(
    r"(?:"
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Inc|Ltd|Corp|Co|Dept|Gov|Gen|Col|Lt|Sgt|Rev|"
    r"No|Fig|Eq|Vol|Ch|Sec|Art|Ref|vs|etc|al|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"Mon|Tue|Wed|Thu|Fri|Sat|Sun)\."
    r"|\b(?:e\.g|i\.e|a\.m|p\.m|Ph\.D|M\.D|B\.A|M\.A)\."
    r"|(?:[A-Za-z]\.){2,}"
    r"|\b[A-Z]\."
    r"|^\(?\d{1,3}\."
    r")$",
    re.IGNORECASE,
)


def split_sentences(text):
    """Split ``text`` into sentences without breaking common abbreviations,
    initials, acronyms (e.g. ``Dr.``, ``Inc.``, ``U.S.``, ``Fig. 2``), or
    numbered list prefixes (``1. First item``)."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    raw_parts = [s for s in _SENTENCE_SPLIT_RE.split(cleaned) if s]
    if len(raw_parts) <= 1:
        return raw_parts
    merged = [raw_parts[0]]
    for part in raw_parts[1:]:
        if _ABBREV_END_RE.search(merged[-1]):
            merged[-1] = merged[-1] + " " + part
        else:
            merged.append(part)
    return merged


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
            hyps = _translate_ct2_bucketed(tr, tokenized)
            return [_clean_mt_output(sp_tgt.decode(hyp), d) for hyp in hyps]

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


def _translate_ct2_bucketed(tr, tokenized, *, target_prefix_token=None):
    """Run ``tr.translate_batch()`` with length-adaptive ``max_decoding_length``,
    ``repetition_penalty=1.2``, and a tighter retry if a short input hallucinates
    an abnormally long tail."""
    if not tokenized:
        return []
    buckets = {"short": [], "med": [], "long": []}
    for idx, toks in enumerate(tokenized):
        n = len(toks)
        if n <= 16:
            buckets["short"].append((idx, toks))
        elif n <= 64:
            buckets["med"].append((idx, toks))
        else:
            buckets["long"].append((idx, toks))

    hyps = [None] * len(tokenized)
    for name, items in buckets.items():
        if not items:
            continue
        batch_toks = [t for _, t in items]
        max_src = max(len(t) for t in batch_toks)
        if name == "short":
            max_dec = max(36, min(64, int(max_src * 3.0) + 12))
        elif name == "med":
            max_dec = max(96, min(192, int(max_src * 2.6) + 16))
        else:
            max_dec = min(512, max(200, int(max_src * 2.5) + 16))
        kwargs = {
            "beam_size": 4,
            "repetition_penalty": 1.2,
            "replace_unknowns": True,
            "no_repeat_ngram_size": 4,
            "max_decoding_length": max_dec,
        }
        if target_prefix_token is not None:
            kwargs["target_prefix"] = [[target_prefix_token]] * len(batch_toks)
        results = tr.translate_batch(batch_toks, **kwargs)
        for (orig_idx, src_toks), res in zip(items, results):
            hyp = list(res.hypotheses[0])
            # Guard against short-heading runaway hallucinations (> 3.5x source token count)
            if len(src_toks) <= 24 and len(hyp) > max(32, int(len(src_toks) * 3.5)):
                retry_kwargs = {
                    "beam_size": 2,
                    "repetition_penalty": 1.35,
                    "replace_unknowns": True,
                    "no_repeat_ngram_size": 3,
                    "max_decoding_length": max(24, int(len(src_toks) * 2.0) + 8),
                }
                if target_prefix_token is not None:
                    retry_kwargs["target_prefix"] = [[target_prefix_token]]
                retry_res = tr.translate_batch([src_toks], **retry_kwargs)
                if retry_res and retry_res[0].hypotheses:
                    hyp = list(retry_res[0].hypotheses[0])
            hyps[orig_idx] = hyp
    return hyps


_CJK_LANGS = {"zh", "zh-Hans", "zh-Hant", "ja", "ko"}
_CJK_FULLWIDTH_LANGS = {"zh", "zh-Hans", "zh-Hant", "ja"}
_CJK_CHAR_RE = re.compile(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af]")
_CJK_SPACE_RE = re.compile(
    r"(?<=[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af])\s+"
    r"(?=[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af])"
)
_CJK_PUNCT_MAP = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}
_CJK_ASCII_PUNCT_RE = re.compile(
    r"(?<=[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef])\s*([,;:!?])(?=\s|$|[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef])"
)
_CJK_TRAILING_DOT_RE = re.compile(r"(?<=[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef])\s*\.(?=\s|$)")
_CJK_POST_PUNCT_SPACE_RE = re.compile(r"(?<=[，。；：！？、」』）])\s+(?=[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef])")
_LATIN_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?)\]])")
_LATIN_AFTER_OPEN_RE = re.compile(r"([(\[])\s+")
_BULLET_LINE_RE = re.compile(r"^(?:[\u2022\u2023\u25e6\u2043\u2219•▪▸►\-*–—]|\d+[.)]|[A-Za-z][.)])\s+")


def _is_cjk_lang(lang):
    norm = i18n.normalize_code(lang or "")
    return norm in _CJK_LANGS or norm.split("-", 1)[0] in _CJK_LANGS


def _is_cjk_fullwidth_lang(lang):
    norm = i18n.normalize_code(lang or "")
    return norm in _CJK_FULLWIDTH_LANGS or norm.split("-", 1)[0] in _CJK_FULLWIDTH_LANGS


def _clean_mt_output(text, dst_lang):
    """Strip leaked SentencePiece ``▁`` markers, normalize whitespace, and polish
    target-language punctuation (full-width CJK punctuation or clean Latin spacing)."""
    out = str(text or "").replace("\u2581", " ")
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    if _is_cjk_lang(dst_lang):
        out = _CJK_SPACE_RE.sub("", out)
        if _is_cjk_fullwidth_lang(dst_lang):
            out = _CJK_ASCII_PUNCT_RE.sub(lambda m: _CJK_PUNCT_MAP[m.group(1)], out)
            out = _CJK_TRAILING_DOT_RE.sub("。", out)
            out = _CJK_POST_PUNCT_SPACE_RE.sub("", out)
    else:
        out = _LATIN_BEFORE_PUNCT_RE.sub(r"\1", out)
        out = _LATIN_AFTER_OPEN_RE.sub(r"\1", out)
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


_FLORES200_CODES = {
    "en": "eng_Latn",
    "zh": "zho_Hans",
    "zh-Hans": "zho_Hans",
    "zh-Hant": "zho_Hant",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "ar": "arb_Arab",
    "pt": "por_Latn",
    "pt-BR": "por_Latn",
    "pt-PT": "por_Latn",
    "ru": "rus_Cyrl",
    "it": "ita_Latn",
    "nl": "nld_Latn",
    "pl": "pol_Latn",
    "tr": "tur_Latn",
    "vi": "vie_Latn",
    "th": "tha_Thai",
    "hi": "hin_Deva",
    "id": "ind_Latn",
    "uk": "ukr_Cyrl",
    "sv": "swe_Latn",
    "da": "dan_Latn",
    "fi": "fin_Latn",
    "no": "nob_Latn",
    "nb": "nob_Latn",
    "cs": "ces_Latn",
    "el": "ell_Grek",
    "he": "heb_Hebr",
    "fa": "pes_Arab",
    "ro": "ron_Latn",
    "hu": "hun_Latn",
    "bg": "bul_Cyrl",
    "hr": "hrv_Latn",
    "sk": "slk_Latn",
    "sl": "slv_Latn",
    "ca": "cat_Latn",
    "ms": "zsm_Latn",
    "tl": "tgl_Latn",
    "bn": "ben_Beng",
    "ta": "tam_Taml",
    "ur": "urd_Arab",
}


def bcp47_to_flores(code):
    """Map a ZPDF BCP-47 tag (e.g. ``en``, ``zh-Hans``, ``es``) to a Meta NLLB-200 FLORES-200 code."""
    norm = i18n.normalize_code(code)
    if norm in _FLORES200_CODES:
        return _FLORES200_CODES[norm]
    base = norm.split("-", 1)[0].lower()
    return _FLORES200_CODES.get(base)


def nllb_model_dir():
    """Return the best installed NLLB-200 CTranslate2 model directory (preferring
    ``nllb-200-1.3b`` over ``nllb-200`` 600M when both are installed), else ``None``."""
    root = i18n.packs_dir()
    for code in (i18n.NLLB_LARGE_PACK_CODE, i18n.NLLB_PACK_CODE):
        for cand in (root / code / "models" / code, root / code):
            if (cand / "model.bin").is_file() and list(cand.glob("*.model")):
                return cand
    return None


def _load_nllb_translator(s, d, mdir):
    """Load Meta NLLB-200 (600M or 1.3B CTranslate2 int8) for direct ``s -> d`` translation."""
    src_flores = bcp47_to_flores(s)
    dst_flores = bcp47_to_flores(d)
    if not src_flores or not dst_flores:
        raise core.PdfManagerError(f"NLLB-200 does not have a language mapping for {s} → {d}")

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

    if not ct2_loaded:
        raise core.PdfManagerError(
            f"found NLLB-200 model at {mdir}, but no offline translation runtime "
            "(ctranslate2 + sentencepiece) is installed. "
            "Update ZPDF, re-run Install ZPDF.cmd, or run: pip install ctranslate2 sentencepiece"
        )

    sp_file = next(
        (mdir / name for name in ("sentencepiece.bpe.model", "sentencepiece.model") if (mdir / name).is_file()),
        None,
    ) or sorted(mdir.glob("*.model"))[0]
    sp = spm.SentencePieceProcessor(model_file=str(sp_file))
    tr = ctranslate2.Translator(str(mdir), device="cpu")

    def _nllb_batch(batch):
        tokenized = [[src_flores, *sp.encode(text, out_type=str), "</s>"] for text in batch]
        hyps = _translate_ct2_bucketed(tr, tokenized, target_prefix_token=dst_flores)
        out = []
        for hyp in hyps:
            if hyp and hyp[0] == dst_flores:
                hyp = hyp[1:]
            out.append(_clean_mt_output(sp.decode(hyp), d))
        return out

    _nllb_batch._neural = True
    _nllb_batch._context_window = 360
    return _nllb_batch


def _parse_llm_json_list(raw_content, expected_len):
    """Extract a JSON list of ``expected_len`` strings from an LLM response."""
    import json

    text = str(raw_content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return None
        try:
            parsed = json.loads(m.group(0))
        except ValueError:
            return None
    if isinstance(parsed, dict):
        for key in ("translations", "items", "result", "output"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if isinstance(parsed, list) and len(parsed) == expected_len:
        return [str(x) for x in parsed]
    return None


def _load_llm_translator(src_lang, dst_lang, *, api_url, model, api_key="", glossary=""):
    """Context-aware batch translator using an OpenAI-compatible ``/chat/completions``
    endpoint (works offline with local Ollama / LM Studio, or online with any BYOK API)."""
    endpoint = str(api_url or "http://localhost:11434/v1").strip().rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    model_name = str(model or "qwen2.5:7b").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    src_label = i18n.BUILTIN_LOCALES.get(src_lang, {}).get("name") or src_lang
    dst_label = i18n.BUILTIN_LOCALES.get(dst_lang, {}).get("name") or dst_lang
    glossary_rules = parse_glossary(glossary)
    glossary_clause = ""
    if glossary_rules:
        lines = [
            f"- Keep '{src}' verbatim." if tgt is None else f"- Always translate '{src}' as '{tgt}'."
            for src, tgt in glossary_rules[:60]
        ]
        glossary_clause = " Follow these mandatory terminology rules:\n" + "\n".join(lines) + "\n"
    system_prompt = (
        f"You are an expert document translator translating from {src_label} ({src_lang}) "
        f"to natural, idiomatic, publication-quality {dst_label} ({dst_lang}). "
        "Maintain consistent terminology and formal document tone across all items. "
        "Keep emails, URLs, phone numbers, dates, currency amounts, numbers, and ID codes verbatim. "
        f"{glossary_clause}"
        "Return ONLY a valid JSON array of translated strings with the exact same length and order as the input array. "
        "Do not add explanations, notes, or markdown."
    )

    def _call_batch(items):
        import json

        payload = {
            "model": model_name,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
            ],
        }
        resp = i18n._http_post_json(endpoint, payload, headers=headers)
        choices = resp.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        parsed = _parse_llm_json_list(content, len(items))
        if parsed is not None:
            return parsed
        if len(items) == 1:
            return [content.strip() or items[0]]
        # Fallback: translate individually if the model returned a mismatched JSON array length
        return [_call_batch([item])[0] for item in items]

    def _llm_batch(batch):
        out = []
        chunk_size = 10
        for i in range(0, len(batch), chunk_size):
            out.extend(_call_batch(list(batch[i : i + chunk_size])))
        return out

    _llm_batch._neural = True
    _llm_batch._llm = True
    _llm_batch._context_window = 1200
    return _llm_batch


def _load_offline_translator(src_lang, dst_lang):
    """Return a batch callable ``fn(list[str]) -> list[str]`` backed by the
    configured translation engine (Smart AI LLM when enabled in Settings,
    otherwise NLLB-200 when installed, otherwise CTranslate2/Argos language
    pack models with automatic English pivot)."""
    s = i18n.normalize_code(src_lang)
    d = i18n.normalize_code(dst_lang)
    if s == d:
        return lambda batch: list(batch)

    from pdfmanager import update as _update

    cfg = _update.settings()
    if (cfg.get("translate_engine") or "offline") == "llm":
        return _load_llm_translator(
            s,
            d,
            api_url=cfg.get("translate_api_url") or "http://localhost:11434/v1",
            model=cfg.get("translate_model") or "qwen2.5:7b",
            api_key=cfg.get("translate_api_key") or "",
            glossary=cfg.get("translate_glossary") or "",
        )

    # Prefer NLLB-200 universal high-quality model when installed and both languages are supported
    nllb_dir = nllb_model_dir()
    if nllb_dir is not None and bcp47_to_flores(s) and bcp47_to_flores(d):
        return _load_nllb_translator(s, d, nllb_dir)

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
_GLOSSARY_SEP_RE = re.compile(r"\s*(?:=>|->|=|\t)\s*")
_STRUCTURAL_BULLET_RE = re.compile(r"^((?:[\u2022\u2023\u25e6\u2043\u2219•▪▸►\-*–—]|\(?\d{1,3}[.)]|\(?[A-Za-z][.)])\s+)")
_LABEL_COLON_RE = re.compile(r"^([^:：\n]{1,42})\s*([:：])\s*(.+)$")
_COMMON_SHORT_CAPS = {
    "A", "AN", "AND", "ARE", "AS", "AT", "BE", "BY", "DUE", "END", "FEE", "FOR",
    "IN", "IS", "NEW", "NO", "NOT", "OF", "ON", "OR", "OUR", "PAY", "PER", "SUM",
    "TAX", "THE", "TO", "ALL", "ANY", "OUT", "USE", "VIA", "WHO", "WHY", "HOW",
}


def parse_glossary(raw_text):
    """Parse newline-separated glossary / keep-verbatim rules.

    Each non-empty line (ignoring ``#`` comments) is either:
    - ``Term`` → keep verbatim (``(term, None)``)
    - ``Source = Target`` / ``Source -> Target`` / ``Source => Target`` → force
      exact translation (``(source, target)``)

    Returns a list of ``(source, target_or_none)`` sorted longest-source-first.
    """
    rules = []
    seen = set()
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _GLOSSARY_SEP_RE.search(line)
        if m:
            src = line[: m.start()].strip()
            tgt = line[m.end() :].strip()
            if src and tgt and src.lower() not in seen:
                seen.add(src.lower())
                rules.append((src, tgt))
        else:
            if line.lower() not in seen:
                seen.add(line.lower())
                rules.append((line, None))
    rules.sort(key=lambda item: len(item[0]), reverse=True)
    return rules


def _compile_glossary_patterns(glossary_rules):
    compiled = []
    for src, tgt in glossary_rules or ():
        escaped = re.escape(src)
        if re.search(r"[A-Za-z0-9]", src[0]) and re.search(r"[A-Za-z0-9]", src[-1]):
            pat = re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
        else:
            pat = re.compile(escaped, re.IGNORECASE)
        compiled.append((pat, tgt))
    return compiled


def _normalize_all_caps(text):
    """Convert multi-word ALL-CAPS prose headings (e.g. ``EXECUTIVE SUMMARY``) to
    sentence case so neural MT translates them instead of copying them as acronyms,
    while preserving 2–3 letter acronyms (e.g. ``PDF``, ``USA``, ``API``)."""
    if not text or any(ch.islower() for ch in text):
        return text
    alpha_words = re.findall(r"[A-Za-z]{2,}", text)
    if len(alpha_words) < 2 or sum(len(w) for w in alpha_words) < 6:
        return text
    first_done = False

    def _fix_word(match):
        nonlocal first_done
        w = match.group(0)
        if len(w) <= 3 and w not in _COMMON_SHORT_CAPS:
            first_done = True
            return w
        if not first_done:
            first_done = True
            return w[0].upper() + w[1:].lower()
        return w.lower()

    return re.sub(r"\b[A-Z]{2,}\b", _fix_word, text)


def _split_structural_affixes(text, dst_lang):
    """Separate leading bullet/number markers and trailing ``: <verbatim>`` data
    values so neural MT translates only the prose label or list item body."""
    prefix = ""
    suffix = ""
    body = text
    m_bul = _STRUCTURAL_BULLET_RE.match(body)
    if m_bul:
        prefix = m_bul.group(1)
        body = body[m_bul.end() :]
    m_lbl = _LABEL_COLON_RE.match(body)
    if m_lbl:
        lhs, _colon, rhs = m_lbl.group(1).strip(), m_lbl.group(2), m_lbl.group(3).strip()
        if lhs and rhs and not rhs.startswith("//") and _PROSE_WORD_RE.search(lhs) and _is_verbatim_block(rhs):
            sep = "：" if _is_cjk_fullwidth_lang(dst_lang) else ": "
            body = lhs
            suffix = f"{sep}{rhs}"
    return prefix, body, suffix


def _chunk_sentences_for_context(sents, max_chars):
    """Group consecutive sentences into multi-sentence chunks up to ``max_chars``
    so context-capable models (NLLB-200, LLM) translate with paragraph context."""
    if not sents or max_chars <= 0 or len(sents) <= 1:
        return list(sents)
    chunks = []
    cur = sents[0]
    for s in sents[1:]:
        cand = f"{cur} {s}"
        if len(cand) <= max_chars:
            cur = cand
        else:
            chunks.append(cur)
            cur = s
    chunks.append(cur)
    return chunks


def _is_verbatim_block(text):
    """Return True when ``text`` contains only non-translatable data (numbers,
    currency, dates, emails, URLs, phone numbers, codes, punctuation)."""
    stripped = _NEURAL_PROTECT_RE.sub(" ", text)
    return _PROSE_WORD_RE.search(stripped) is None


def _find_protected_spans(text, compiled_glossary=None):
    """Return non-overlapping ``(start, end, replacement_value)`` spans for all
    protected entities and glossary terms in ``text``."""
    spans = []

    def _overlaps(s, e):
        return any(not (e <= es or s >= ee) for es, ee, _ in spans)

    for m in _NEURAL_PROTECT_RE.finditer(text):
        spans.append((m.start(), m.end(), m.group(0)))
    for pat, tgt in compiled_glossary or ():
        for m in pat.finditer(text):
            if not _overlaps(m.start(), m.end()):
                spans.append((m.start(), m.end(), tgt if tgt is not None else m.group(0)))
    spans.sort(key=lambda item: item[0])
    return spans


def _protect_neural_entities(text, compiled_glossary=None):
    """Replace emails, URLs, phone numbers, codes, ISO dates, currency, and
    glossary terms with collision-free 6-digit numeric sentinels."""
    spans = _find_protected_spans(text, compiled_glossary)
    if not spans:
        return text, []
    mapping = []
    next_id = 910001
    out_parts = []
    pos = 0
    for start, end, repl_val in spans:
        while str(next_id) in text:
            next_id += 1
        sentinel = str(next_id)
        next_id += 1
        out_parts.append(text[pos:start])
        out_parts.append(sentinel)
        mapping.append((sentinel, repl_val))
        pos = end
    out_parts.append(text[pos:])
    return "".join(out_parts), mapping


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


def _translate_around_entities(text, fn, joiner, compiled_glossary=None):
    """Fallback when a neural model drops a sentinel: translate only the prose
    segments between protected entities/glossary terms so every span stays exact."""
    spans = _find_protected_spans(text, compiled_glossary)
    pieces = []
    prose_indices = []
    prose_texts = []
    pos = 0
    for start, end, repl_val in spans:
        before = text[pos:start]
        if before.strip():
            prose_indices.append(len(pieces))
            prose_texts.append(before.strip())
            pieces.append("")
        elif before:
            pieces.append(before)
        pieces.append(repl_val)
        pos = end
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


def translate_texts(texts, src_lang, dst_lang, *, translator=None, batch_size=32, glossary=None):
    """Translate a sequence of strings offline, preserving emails, URLs, phone
    numbers, reference codes, dates, currency, bullets, key-value labels, and
    optional user glossary terms, while deduplicating identical chunks."""
    fn = translator if translator is not None else _load_offline_translator(src_lang, dst_lang)
    is_neural = bool(getattr(fn, "_neural", False))
    is_llm = bool(getattr(fn, "_llm", False))
    context_window = int(getattr(fn, "_context_window", 0) or 0)
    joiner = "" if _is_cjk_lang(dst_lang) else " "

    if glossary is None and is_neural and not is_llm:
        from pdfmanager import update as _update

        glossary = _update.settings().get("translate_glossary") or ""
    compiled_glossary = _compile_glossary_patterns(parse_glossary(glossary)) if (is_neural and not is_llm) else []

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
        if is_neural and is_llm:
            sents = _chunk_sentences_for_context(split_sentences(text_str), context_window)
            per_text_plan.append(("neural", "", "", text_str, sents, ()))
        elif is_neural:
            prefix, body, suffix = _split_structural_affixes(text_str, dst_lang)
            if not body.strip() or _is_verbatim_block(body):
                per_text_plan.append(("verbatim", text_str))
                continue
            norm_body = _normalize_all_caps(body)
            masked, mapping = _protect_neural_entities(norm_body, compiled_glossary)
            sents = _chunk_sentences_for_context(split_sentences(masked), context_window)
            per_text_plan.append(("neural", prefix, suffix, norm_body, sents, mapping))
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
            _, prefix, suffix, orig_body, sents, mapping = item
            joined = joiner.join(cache.get(s, s) for s in sents)
            if mapping:
                restored, ok = _restore_neural_entities(joined, mapping)
                if not ok:
                    restored = _translate_around_entities(orig_body, fn, joiner, compiled_glossary)
                results.append(f"{prefix}{restored}{suffix}")
            else:
                results.append(f"{prefix}{joined}{suffix}")
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


def _detect_font_style(words):
    """Infer ``(family, bold, italic)`` from pdfplumber word ``fontname`` attributes."""
    bold_hits = 0
    italic_hits = 0
    serif_hits = 0
    mono_hits = 0
    for w in words:
        fn = str(w.get("fontname") or "").lower()
        if any(k in fn for k in ("bold", "heavy", "black", "demi", "semibold", "medi")):
            bold_hits += 1
        if any(k in fn for k in ("italic", "oblique", "slant")):
            italic_hits += 1
        if any(k in fn for k in ("mono", "courier", "consolas", "menlo")):
            mono_hits += 1
        elif any(k in fn for k in ("times", "serif", "georgia", "garamond", "cambria", "mincho", "song")):
            serif_hits += 1
    half = max(1, len(words) // 2)
    bold = bold_hits >= half
    italic = italic_hits >= half
    if mono_hits >= half:
        family = "mono"
    elif serif_hits >= half:
        family = "serif"
    else:
        family = "sans"
    return family, bold, italic


def _detect_align(x0, x1, container_x0, container_x1, *, is_cell=False, lines=1, solo_row=True):
    """Detect ``left``, ``center``, or ``right`` alignment inside a cell or page."""
    span_w = max(1.0, container_x1 - container_x0)
    left_m = x0 - container_x0
    right_m = container_x1 - x1
    if is_cell:
        if left_m > 4.0 and right_m > 4.0 and abs(left_m - right_m) <= max(4.0, span_w * 0.08):
            return "center"
        if right_m <= max(16.0, span_w * 0.12) and left_m > right_m + 10.0:
            return "right"
        return "left"
    if lines == 1 and solo_row:
        cx = (x0 + x1) / 2.0
        page_cx = (container_x0 + container_x1) / 2.0
        if (
            abs(cx - page_cx) <= 12.0
            and abs(left_m - right_m) <= 24.0
            and left_m > span_w * 0.12
            and right_m > span_w * 0.12
        ):
            return "center"
    return "left"


def _extract_layout_blocks(plumber_page):
    """Extract positioned text blocks from a ``pdfplumber`` page, respecting
    real multi-row/multi-column table cells, columns, headings, font sizes,
    bold/italic styles, and left/center/right alignment."""
    words = plumber_page.extract_words(
        extra_attrs=["size", "fontname", "non_stroking_color"],
        keep_blank_chars=False,
        use_text_flow=False,
    )
    if not words:
        return []

    page_w = float(plumber_page.width or 612.0)
    page_h = float(plumber_page.height or 792.0)

    # 1. Detect genuine multi-row, multi-column tables (ignore 1x1 page/section border boxes)
    table_cells = []
    try:
        for tbl in plumber_page.find_tables() or ():
            if len(getattr(tbl, "rows", ()) or ()) < 2 or len(getattr(tbl, "columns", ()) or ()) < 2:
                continue
            for cell in tbl.cells or ():
                if cell and len(cell) == 4:
                    cx0, ctop, cx1, cbottom = (float(v) for v in cell)
                    cw, ch = cx1 - cx0, cbottom - ctop
                    if 8.0 <= cw <= page_w * 0.80 and 6.0 <= ch <= page_h * 0.25:
                        table_cells.append((cx0, ctop, cx1, cbottom))
    except Exception:  # noqa: BLE001
        table_cells = []

    cell_words = {}
    free_words = []
    for w in words:
        wx = (float(w["x0"]) + float(w["x1"])) / 2.0
        wy = (float(w["top"]) + float(w["bottom"])) / 2.0
        assigned = None
        for ci, (cx0, ctop, cx1, cbottom) in enumerate(table_cells):
            if cx0 - 1.0 <= wx <= cx1 + 1.0 and ctop - 1.0 <= wy <= cbottom + 1.0:
                assigned = ci
                break
        if assigned is not None:
            cell_words.setdefault(assigned, []).append(w)
        else:
            free_words.append(w)

    blocks = []

    # Build one strictly-bounded block per genuine homogeneous table cell;
    # if a cell contains mixed font sizes (e.g. heading + body) or large paragraph gaps,
    # return its words to free_words so every sub-block keeps its exact position.
    for ci, cwords in sorted(cell_words.items()):
        cx0, ctop, cx1, cbottom = table_cells[ci]
        cwords.sort(key=lambda w: (round(float(w["top"]) / 2.5), float(w["x0"])))
        sizes = [max(4.0, float(w.get("size") or 10.0)) for w in cwords]
        min_sz, max_sz = min(sizes), max(sizes)
        avg_size = sum(sizes) / len(sizes)
        if (max_sz - min_sz) > max(2.0, min_sz * 0.25):
            free_words.extend(cwords)
            continue
        # Check for large internal vertical gaps (multi-paragraph container box)
        has_big_gap = False
        prev_bot = float(cwords[0]["bottom"])
        for cw in cwords[1:]:
            if float(cw["top"]) - prev_bot > max(12.0, avg_size * 1.4):
                has_big_gap = True
                break
            prev_bot = max(prev_bot, float(cw["bottom"]))
        if has_big_gap:
            free_words.extend(cwords)
            continue

        family, bold, italic = _detect_font_style(cwords)
        wx0 = min(float(w["x0"]) for w in cwords)
        wx1 = max(float(w["x1"]) for w in cwords)
        wtop = min(float(w["top"]) for w in cwords)
        wbot = max(float(w["bottom"]) for w in cwords)
        line_count = max(1, int(round((wbot - wtop) / max(1.0, avg_size * 1.15))))
        align = _detect_align(wx0, wx1, cx0, cx1, is_cell=True, lines=line_count)
        blocks.append({
            "text": " ".join(w["text"] for w in cwords),
            "x0": max(cx0 + 2.0, wx0 if align == "left" else cx0 + 2.0),
            "top": max(ctop + 1.0, wtop),
            "x1": min(cx1 - 2.0, wx1 if align == "right" else cx1 - 2.0),
            "bottom": min(cbottom - 1.0, max(wbot, cbottom - 1.0)),
            "box_x0": cx0 + 2.0,
            "box_x1": cx1 - 2.0,
            "size": avg_size,
            "color": _normalize_color(cwords[0].get("non_stroking_color")),
            "family": family,
            "bold": bold,
            "italic": italic,
            "align": align,
            "lines": line_count,
            "in_table": True,
        })

    if not free_words:
        blocks.sort(key=lambda b: (b["top"], b["x0"]))
        return blocks

    # 2. Group non-table words into visual lines by vertical top coordinate
    free_words.sort(key=lambda w: (round(float(w["top"]) / 2.5), float(w["x0"])))
    raw_lines = []
    for w in free_words:
        w_top = float(w["top"])
        w_size = max(4.0, float(w.get("size") or 10.0))
        if raw_lines and abs(w_top - raw_lines[-1]["top"]) <= max(3.0, raw_lines[-1]["size"] * 0.45):
            raw_lines[-1]["words"].append(w)
        else:
            raw_lines.append({"top": w_top, "size": w_size, "words": [w]})

    # 3. Split each visual line on large horizontal gaps (separates columns)
    seg_objs = []
    for ln in raw_lines:
        lwords = sorted(ln["words"], key=lambda w: float(w["x0"]))
        line_segs = []
        cur = [lwords[0]]
        for w in lwords[1:]:
            prev = cur[-1]
            prev_size = max(4.0, float(prev.get("size") or 10.0))
            gap = float(w["x0"]) - float(prev["x1"])
            if gap > max(18.0, prev_size * 1.8):
                line_segs.append(cur)
                cur = [w]
            else:
                cur.append(w)
        line_segs.append(cur)
        solo_row = len(line_segs) == 1
        for seg in line_segs:
            sizes = [max(4.0, float(w.get("size") or 10.0)) for w in seg]
            avg_size = sum(sizes) / len(sizes)
            family, bold, italic = _detect_font_style(seg)
            sx0 = min(float(w["x0"]) for w in seg)
            sx1 = max(float(w["x1"]) for w in seg)
            seg_objs.append({
                "text": " ".join(w["text"] for w in seg),
                "x0": sx0,
                "top": min(float(w["top"]) for w in seg),
                "x1": sx1,
                "bottom": max(float(w["bottom"]) for w in seg),
                "box_x0": sx0,
                "box_x1": sx1,
                "size": avg_size,
                "color": _normalize_color(seg[0].get("non_stroking_color")),
                "family": family,
                "bold": bold,
                "italic": italic,
                "align": _detect_align(sx0, sx1, 0.0, page_w, is_cell=False, lines=1, solo_row=solo_row),
                "lines": 1,
                "in_table": False,
            })

    # 4. Merge vertically adjacent line segments in the same column & style into blocks
    seg_objs.sort(key=lambda s: (s["top"], s["x0"]))
    free_blocks = []
    for seg in seg_objs:
        merged = False
        if not _BULLET_LINE_RE.match(seg["text"]):
            for blk in reversed(free_blocks):
                v_gap = seg["top"] - blk["bottom"]
                if v_gap > max(7.0, blk["size"] * 0.85):
                    continue
                if v_gap < -blk["size"] * 0.35:
                    continue
                same_size = abs(seg["size"] - blk["size"]) <= max(1.0, blk["size"] * 0.15)
                # Allow typical first-line paragraph indent (up to 36pt) on line 1 -> line 2
                left_tol = max(36.0, blk["size"] * 3.0) if blk["lines"] == 1 else max(18.0, blk["size"] * 2.0)
                same_left = abs(seg["x0"] - blk["x0"]) <= left_tol
                horiz_overlap = min(seg["x1"], blk["x1"]) > max(seg["x0"], blk["x0"])
                blk_w = blk["x1"] - blk["x0"]
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
                    blk["box_x0"] = blk["x0"]
                    blk["box_x1"] = blk["x1"]
                    blk["bottom"] = max(blk["bottom"], seg["bottom"])
                    blk["lines"] += 1
                    blk["align"] = "left"
                    merged = True
                    break
        if not merged:
            free_blocks.append(seg)

    blocks.extend(free_blocks)
    blocks.sort(key=lambda b: (b["top"], b["x0"]))
    return blocks


def _strip_bt_et_ops(instructions):
    """Filter out all PDF text blocks (``BT`` ... ``ET``) while preserving all
    images, shapes, tables, colors, gradients, and graphics state operators."""
    kept = []
    in_text = False
    for operands, operator in instructions:
        op = str(operator)
        if op == "BT":
            in_text = True
            continue
        if op == "ET":
            in_text = False
            continue
        if not in_text:
            kept.append((operands, operator))
    return kept


def _strip_text_from_pdf(src_path, page_indices, password=None):
    """Return a ``pypdf.PdfReader`` over a copy of ``src_path`` with all
    ``BT ... ET`` text operators removed from ``page_indices`` (while keeping
    all images, tables, borders, shadings, gradients, and forms intact), or
    ``None`` if stripping fails."""
    if not page_indices:
        return None
    try:
        import pikepdf
        from pypdf import PdfReader

        with core.open_pikepdf(src_path, password) as pdf:
            for idx in page_indices:
                if idx < 0 or idx >= len(pdf.pages):
                    continue
                page = pdf.pages[idx]
                ops = pikepdf.parse_content_stream(page)
                kept = _strip_bt_et_ops(ops)
                page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(kept))
                # Also strip text inside non-shared Form XObjects on this page
                res = page.get("/Resources")
                xobjs = res.get("/XObject") if res and "/XObject" in res else None
                if xobjs:
                    new_xobjs = None
                    for name, ref in list(xobjs.items()):
                        if isinstance(ref, pikepdf.Stream) and ref.get("/Subtype") == "/Form":
                            try:
                                fops = pikepdf.parse_content_stream(ref)
                                fkept = _strip_bt_et_ops(fops)
                                if len(fkept) != len(fops):
                                    cloned = pdf.make_stream(pikepdf.unparse_content_stream(fkept))
                                    for k, v in ref.items():
                                        if k not in ("/Length", "/Filter", "/DecodeParms"):
                                            cloned[k] = v
                                    if new_xobjs is None:
                                        new_xobjs = pikepdf.Dictionary(xobjs)
                                    new_xobjs[name] = cloned
                            except Exception:  # noqa: BLE001
                                continue
                    if new_xobjs is not None:
                        new_res = pikepdf.Dictionary(res)
                        new_res["/XObject"] = new_xobjs
                        page["/Resources"] = new_res
            buf = io.BytesIO()
            pdf.save(buf)
        buf.seek(0)
        return PdfReader(buf)
    except Exception:  # noqa: BLE001
        return None


def _make_layout_overlay(width, height, blocks, translated_texts, bg_image=None, *, draw_bg=False):
    """Build a transparent PDF overlay page that draws each translated block
    in-place with original font size, bold/italic style, color, alignment, and
    CJK-aware auto-fit wrapping. When ``draw_bg=True`` (fallback when content-
    stream text stripping was unavailable), paints sampled background rects first."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))

    for idx, (blk, trans) in enumerate(zip(blocks, translated_texts)):
        text = (trans or "").strip()
        if not text:
            continue
        x0, top, x1, bottom = blk["x0"], blk["top"], blk["x1"], blk["bottom"]
        box_x0 = blk.get("box_x0", x0)
        box_x1 = blk.get("box_x1", x1)
        orig_size = max(5.5, float(blk["size"]))
        pad = min(2.0, orig_size * 0.18)
        bg_rgb = _sample_bg_rgb(bg_image, x0, top, x1, bottom, width, height) if bg_image is not None else None

        if draw_bg:
            cover_rgb = bg_rgb if bg_rgb is not None else (1.0, 1.0, 1.0)
            rect_x = max(0.0, x0 - pad)
            rect_y = max(0.0, height - bottom - pad)
            rect_w = min(width - rect_x, (x1 - x0) + 2 * pad)
            rect_h = min(height - rect_y, (bottom - top) + 2 * pad)
            c.setFillColorRGB(*cover_rgb)
            c.rect(rect_x, rect_y, rect_w, rect_h, stroke=0, fill=1)

        # Compute available width: strictly cell-bounded inside tables;
        # outside tables, single-line labels/headings may expand into free space.
        avail_w = max(18.0, box_x1 - box_x0)
        align = blk.get("align", "left")
        if not blk.get("in_table") and blk["lines"] == 1:
            if align == "center":
                half_span = min((x0 + x1) / 2.0 - 18.0, width - 18.0 - (x0 + x1) / 2.0)
                avail_w = max(avail_w, half_span * 2.0)
            elif align == "right":
                left_limit = 18.0
                for j, other in enumerate(blocks):
                    if j == idx:
                        continue
                    if other["top"] < bottom and other["bottom"] > top and other["x1"] <= x0 + 2.0:
                        left_limit = max(left_limit, other["x1"] + 6.0)
                avail_w = max(avail_w, x1 - left_limit)
            else:
                right_limit = width - 18.0
                for j, other in enumerate(blocks):
                    if j == idx:
                        continue
                    if other["top"] < bottom and other["bottom"] > top and other["x0"] >= x1 - 2.0:
                        right_limit = min(right_limit, other["x0"] - 6.0)
                avail_w = max(avail_w, right_limit - x0)

        avail_h = max(orig_size * 1.25, (bottom - top) + 2 * pad)
        chosen_font = fonts.font_for(
            text,
            family=blk.get("family", "sans"),
            bold=bool(blk.get("bold", False)),
            italic=bool(blk.get("italic", False)),
        )

        # Auto-fit font size down gently until wrapped lines fit inside the block
        min_size = max(5.5, orig_size * 0.55)
        fit_size = orig_size
        wrapped = _wrap_text(text, chosen_font, fit_size, avail_w)
        while len(wrapped) * (fit_size * 1.2) > avail_h + 1.0 and fit_size > min_size:
            fit_size = max(min_size, fit_size * 0.9)
            wrapped = _wrap_text(text, chosen_font, fit_size, avail_w)

        fg_rgb = blk["color"]
        if bg_rgb is not None and abs(_luminance(fg_rgb) - _luminance(bg_rgb)) < 0.25:
            fg_rgb = (0.0, 0.0, 0.0) if _luminance(bg_rgb) > 0.5 else (1.0, 1.0, 1.0)

        c.setFillColorRGB(*fg_rgb)
        c.setFont(chosen_font, fit_size)
        leading = fit_size * 1.2
        y = height - top - fit_size * 0.88
        cx = (box_x0 + box_x1) / 2.0
        for line in wrapped:
            if y < max(4.0, height - bottom - pad - leading * 0.35):
                break
            if align == "center":
                c.drawCentredString(cx, y, line)
            elif align == "right":
                c.drawRightString(box_x1, y, line)
            else:
                c.drawString(box_x0, y, line)
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
    # borders, columns, alignment, bold/italic styles, font sizes, and colors stay intact.
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

    # Strip original BT..ET text operators from pages that have positioned blocks so
    # zero ghost original text remains underneath and no opaque whiteout patches are needed.
    block_page_indices = {i for i, blks in enumerate(pages_blocks) if blks}
    stripped_reader = _strip_text_from_pdf(src, block_page_indices, password=password)
    use_stripped = stripped_reader is not None and len(stripped_reader.pages) == len(reader.pages)

    # Optional 72-dpi render for contrast checking / fallback background cover rects
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
                    overlay = _make_layout_overlay(w, h, blks, trans_blks, bg_image=bg_img, draw_bg=not use_stripped)
                    base_right = stripped_reader.pages[idx] if use_stripped else orig_page
                    shift = Transformation().translate(w, 0)
                    spread.merge_transformed_page(base_right, shift)
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

        # in-place mode: overlay translated blocks onto text-stripped original pages
        # (preserving images, tables, borders, colors, and layout with zero ghost text)
        for idx, (orig_page, blks, trans_blks) in enumerate(zip(reader.pages, pages_blocks, pages_trans_blocks)):
            w = float(orig_page.mediabox.width)
            h = float(orig_page.mediabox.height)
            if blks:
                bg_img = _render_page_bg(pdfium_doc, idx)
                overlay = _make_layout_overlay(w, h, blks, trans_blks, bg_image=bg_img, draw_bg=not use_stripped)
                base_page = stripped_reader.pages[idx] if use_stripped else orig_page
                attached = writer.add_page(base_page)
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
