# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Translation engines: CTranslate2 OPUS-MT/Argos, MADLAD-400, OpenAI-compatible LLM, and router."""

import re
import sys
from pathlib import Path

from pdfmanager import core, i18n, progress
from pdfmanager.translate.models import (
    _get_cached_ct2, _load_tokenizer, ensure_runtime, model_dir_for,
)

_APPROX_PACK_MB = 45

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
            sp_src_path, sp_tgt_path = sp_src_file, sp_tgt_file
        else:
            sp_files = sorted(mdir.glob("*.model")) or sorted(ct2_dir.glob("*.model"))
            if not sp_files:
                raise core.PdfManagerError(
                    f"found model folder for {s} → {d} at {mdir}, but no tokenizer (.model) file was found"
                )
            sp_src_path = sp_tgt_path = sp_files[0]

        tr, sp_src, sp_tgt = _get_cached_ct2(ct2_dir, sp_src_path, sp_tgt_path, ctranslate2, spm)
        is_opus_spm = sp_src_path.name == "source.spm"

        def _ct2_batch(batch):
            if is_opus_spm:
                tokenized = [sp_src.encode(text, out_type=str) + ["</s>"] for text in batch]
            else:
                tokenized = [sp_src.encode(text, out_type=str) for text in batch]
            hyps = _translate_ct2_bucketed(tr, tokenized)
            return [
                _clean_mt_output(sp_tgt.decode([t for t in hyp if t not in ("</s>", "<s>", "<unk>")]), d)
                for hyp in hyps
            ]

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


_CT2_SUB_BATCH = 32


def _translate_ct2_bucketed(tr, tokenized, *, target_prefix_token=None, beam_size=4):
    """Run ``tr.translate_batch()`` with length-sorted token-bounded sub-batches,
    length-adaptive ``max_decoding_length``, ``repetition_penalty=1.2``, and a
    tighter retry if a short input hallucinates an abnormally long tail."""
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
        # Sort by token length within each bucket to minimize padding waste.
        items.sort(key=lambda pair: len(pair[1]))
        for start in range(0, len(items), _CT2_SUB_BATCH):
            progress.check()
            sub = items[start : start + _CT2_SUB_BATCH]
            batch_toks = [t for _, t in sub]
            max_src = max(len(t) for t in batch_toks)
            if name == "short":
                max_dec = max(36, min(64, int(max_src * 3.0) + 12))
            elif name == "med":
                max_dec = max(96, min(192, int(max_src * 2.6) + 16))
            else:
                max_dec = min(512, max(200, int(max_src * 2.5) + 16))
            kwargs = {
                "beam_size": beam_size,
                "repetition_penalty": 1.2,
                "replace_unknowns": True,
                "no_repeat_ngram_size": 4,
                "max_decoding_length": max_dec,
            }
            if target_prefix_token is not None:
                kwargs["target_prefix"] = [[target_prefix_token]] * len(batch_toks)
            try:
                results = tr.translate_batch(batch_toks, max_batch_size=2048, batch_type="tokens", **kwargs)
            except TypeError:
                results = tr.translate_batch(batch_toks, **kwargs)
            for (orig_idx, src_toks), res in zip(sub, results):
                hyp = list(res.hypotheses[0])
                # Guard against short-heading runaway hallucinations (> 3.5x source token count)
                if len(src_toks) <= 24 and len(hyp) > max(32, int(len(src_toks) * 3.5)):
                    progress.check()
                    retry_kwargs = {
                        "beam_size": min(2, beam_size),
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


_MADLAD_CODES = {
    "en": "en",
    "zh": "zh",
    "zh-Hans": "zh",
    "zh-CN": "zh",
    "zh-Hant": "zh_Hant",
    "zh-TW": "zh_Hant",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "ja": "ja",
    "ko": "ko",
    "ar": "ar",
    "pt": "pt",
    "pt-BR": "pt",
    "pt-PT": "pt",
    "ru": "ru",
    "it": "it",
    "nl": "nl",
    "pl": "pl",
    "tr": "tr",
    "vi": "vi",
    "th": "th",
    "hi": "hi",
    "id": "id",
    "uk": "uk",
    "sv": "sv",
    "da": "da",
    "fi": "fi",
    "no": "no",
    "nb": "no",
    "nn": "no",
    "cs": "cs",
    "el": "el",
    "he": "iw",
    "iw": "iw",
    "fa": "fa",
    "ro": "ro",
    "hu": "hu",
    "bg": "bg",
    "hr": "hr",
    "sr": "sr",
    "bs": "bs",
    "sk": "sk",
    "sl": "sl",
    "sq": "sq",
    "mk": "mk",
    "be": "be",
    "ca": "ca",
    "eu": "eu",
    "gl": "gl",
    "cy": "cy",
    "ga": "ga",
    "is": "is",
    "et": "et",
    "lv": "lv",
    "lt": "lt",
    "mt": "mt",
    "lb": "lb",
    "ms": "ms",
    "tl": "tl",
    "fil": "tl",
    "bn": "bn",
    "ta": "ta",
    "te": "te",
    "mr": "mr",
    "gu": "gu",
    "kn": "kn",
    "ml": "ml",
    "pa": "pa",
    "ur": "ur",
    "ne": "ne",
    "si": "si",
    "my": "my",
    "km": "km",
    "lo": "lo",
    "ka": "ka",
    "hy": "hy",
    "az": "az",
    "kk": "kk",
    "uz": "uz",
    "ky": "ky",
    "tg": "tg",
    "tk": "tk",
    "mn": "mn",
    "ps": "ps",
    "ku": "ku",
    "sd": "sd",
    "yi": "yi",
    "sw": "sw",
    "af": "af",
    "am": "am",
    "yo": "yo",
    "ha": "ha",
    "so": "so",
    "zu": "zu",
    "xh": "xh",
    "ig": "ig",
    "jv": "jv",
    "su": "su",
    "ceb": "ceb",
    "ht": "ht",
    "eo": "eo",
    "la": "la",
}


def bcp47_to_madlad(code):
    """Map a ZPDF BCP-47 tag (e.g. ``en``, ``zh-Hans``, ``zh-Hant``) to a Google MADLAD-400 language tag."""
    norm = i18n.normalize_code(code)
    if norm in _MADLAD_CODES:
        return _MADLAD_CODES[norm]
    base = norm.split("-", 1)[0].lower()
    return _MADLAD_CODES.get(base, base if re.fullmatch(r"[a-z]{2,3}", base) else None)


def madlad_model_dir():
    """Return the best installed Google MADLAD-400 CTranslate2 model directory
    (preferring ``madlad-400-10b`` > ``madlad-400-7b`` > ``madlad-400-3b``,
    Apache-2.0), else ``None``."""
    root = i18n.packs_dir()
    for code in (i18n.MADLAD_10B_PACK_CODE, i18n.MADLAD_7B_PACK_CODE, i18n.MADLAD_PACK_CODE):
        for cand in (root / code / "models" / code, root / code):
            if (cand / "model.bin").is_file() and list(cand.glob("*.model")):
                return cand
    return None


def _load_madlad_translator(s, d, mdir):
    """Load Google MADLAD-400 3B (CTranslate2 int8, Apache-2.0) for direct ``s -> d`` translation."""
    dst_code = bcp47_to_madlad(d)
    if not dst_code:
        raise core.PdfManagerError(f"MADLAD-400 does not have a language mapping for {d}")

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
            f"found MADLAD-400 model at {mdir}, but no offline translation runtime "
            "(ctranslate2 + sentencepiece) is installed. "
            "Update ZPDF, re-run Install ZPDF.cmd, or run: pip install ctranslate2 sentencepiece"
        )

    sp_file = next(
        (mdir / name for name in ("spiece.model", "sentencepiece.model") if (mdir / name).is_file()),
        None,
    ) or sorted(mdir.glob("*.model"))[0]
    tr, sp, _ = _get_cached_ct2(mdir, sp_file, sp_file, ctranslate2, spm)
    dst_tag = f"<2{dst_code}>"

    def _madlad_batch(batch):
        if not batch:
            return []
        unique = []
        index_of = {}
        mapping = []
        for text in batch:
            idx = index_of.get(text)
            if idx is None:
                idx = len(unique)
                index_of[text] = idx
                unique.append(text)
            mapping.append(idx)
        tokenized = [[dst_tag, *sp.encode(text, out_type=str), "</s>"] for text in unique]
        hyps = _translate_ct2_bucketed(tr, tokenized, beam_size=2)
        decoded = [
            _clean_mt_output(sp.decode([t for t in hyp if t not in ("</s>", "<s>", "<unk>")]), d)
            for hyp in hyps
        ]
        return [decoded[i] for i in mapping]

    _madlad_batch._neural = True
    _madlad_batch._context_window = 360
    return _madlad_batch


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
    from pdfmanager.translate.text import parse_glossary
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

        progress.check()
        payload = {
            "model": model_name,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
            ],
        }
        resp = i18n._http_post_json(endpoint, payload, headers=headers, timeout=60)
        choices = resp.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        parsed = _parse_llm_json_list(content, len(items))
        if parsed is not None:
            return parsed
        if len(items) == 1:
            return [content.strip() or items[0]]
        # Fallback: translate individually if the model returned a mismatched JSON array length
        out_single = []
        for item in items:
            progress.check()
            out_single.append(_call_batch([item])[0])
        return out_single

    def _llm_batch(batch):
        out = []
        chunk_size = 10
        for i in range(0, len(batch), chunk_size):
            progress.check()
            out.extend(_call_batch(list(batch[i : i + chunk_size])))
        return out

    _llm_batch._neural = True
    _llm_batch._llm = True
    _llm_batch._context_window = 1200
    return _llm_batch


def _load_offline_translator(src_lang, dst_lang):
    """Return a batch callable ``fn(list[str]) -> list[str]`` backed by the
    configured translation engine (Smart AI LLM when enabled in Settings,
    otherwise MADLAD-400 3B when installed, otherwise OPUS-MT / Argos language
    pack models with automatic English pivot)."""
    s = i18n.normalize_code(src_lang)
    d = i18n.normalize_code(dst_lang)
    if s == d:
        return lambda batch: list(batch)

    from pdfmanager import update as _update

    cfg = _update.settings()
    _pkg = sys.modules.get("pdfmanager.translate")
    _llm_loader = getattr(_pkg, "_load_llm_translator", _load_llm_translator)
    _single_loader = getattr(_pkg, "_load_single_translator", _load_single_translator)
    if (cfg.get("translate_engine") or "offline") == "llm":
        return _llm_loader(
            s,
            d,
            api_url=cfg.get("translate_api_url") or "http://localhost:11434/v1",
            model=cfg.get("translate_model") or "qwen2.5:7b",
            api_key=cfg.get("translate_api_key") or "",
            glossary=cfg.get("translate_glossary") or "",
        )

    _madlad_loader = getattr(_pkg, "_load_madlad_translator", _load_madlad_translator)
    # Prefer MADLAD-400 3B universal model (Apache-2.0) when installed
    madlad_dir = madlad_model_dir()
    if madlad_dir is not None and bcp47_to_madlad(d):
        return _madlad_loader(s, d, madlad_dir)

    mdir = model_dir_for(s, d)
    if mdir is not None:
        return _single_loader(s, d, mdir)

    # Automatic English pivot: src -> en -> dst
    if s != i18n.DEFAULT_LANG and d != i18n.DEFAULT_LANG:
        hop1 = model_dir_for(s, i18n.DEFAULT_LANG)
        hop2 = model_dir_for(i18n.DEFAULT_LANG, d)
        if hop1 is not None and hop2 is not None:
            fn1 = _single_loader(s, i18n.DEFAULT_LANG, hop1)
            fn2 = _single_loader(i18n.DEFAULT_LANG, d, hop2)

            def _pivot_batch(batch):
                return fn2(fn1(batch))

            _pivot_batch._neural = bool(getattr(fn1, "_neural", False) or getattr(fn2, "_neural", False))
            return _pivot_batch

    pack_code = d if d != i18n.DEFAULT_LANG else s
    raise core.PdfManagerError(
        f"translation from {s} to {d} requires the {pack_code!r} language pack "
        f"(~{_APPROX_PACK_MB} MB); download it now to continue"
    )
