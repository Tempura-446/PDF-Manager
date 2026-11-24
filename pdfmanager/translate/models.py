# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Translation model discovery, BPE/SentencePiece tokenizers, CT2 cache, and language detection."""

import re
from pathlib import Path

from pdfmanager import i18n

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
        if len(self._cache) >= 50_000:
            self._cache.clear()
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


import collections
import threading

_CT2_CACHE = collections.OrderedDict()  # key -> (entry, est_bytes)
_CT2_CACHE_LOCK = threading.Lock()
_CT2_KEY_LOCKS = {}
_CT2_CACHE_MAX = 2
_CT2_CACHE_MAX_BYTES = 4_000_000_000  # 4 GB byte budget (allows 1× 3.3B or 2× smaller models)


def clear_model_cache():
    """Release cached CTranslate2 translator models from memory."""
    with _CT2_CACHE_LOCK:
        _CT2_CACHE.clear()


def _get_cached_ct2(ct2_dir, sp_src_path, sp_tgt_path, ctranslate2, spm):
    """Return ``(translator, sp_src, sp_tgt)`` from a byte-budgeted in-memory LRU
    cache with per-key single-flight loading, keyed by model directory, tokenizer
    paths, and ``model.bin`` mtime so re-downloaded packs invalidate stale entries."""
    resolved = str(Path(ct2_dir).resolve())
    model_bin = Path(ct2_dir) / "model.bin"
    try:
        st = model_bin.stat()
        mtime = st.st_mtime_ns
        est_bytes = st.st_size
    except OSError:
        mtime = 0
        est_bytes = 0
    key = (resolved, str(sp_src_path), str(sp_tgt_path), mtime, id(ctranslate2))
    with _CT2_CACHE_LOCK:
        hit = _CT2_CACHE.get(key)
        if hit is not None:
            _CT2_CACHE.move_to_end(key)
            return hit[0]
        key_lock = _CT2_KEY_LOCKS.setdefault(key, threading.Lock())

    with key_lock:
        with _CT2_CACHE_LOCK:
            hit = _CT2_CACHE.get(key)
            if hit is not None:
                _CT2_CACHE.move_to_end(key)
                return hit[0]
        sp_src = _load_tokenizer(sp_src_path, spm)
        sp_tgt = sp_src if sp_tgt_path == sp_src_path else _load_tokenizer(sp_tgt_path, spm)
        tr = None
        try:
            if hasattr(ctranslate2, "get_cuda_device_count") and ctranslate2.get_cuda_device_count() > 0:
                tr = ctranslate2.Translator(str(ct2_dir), device="cuda")
        except Exception:  # noqa: BLE001 - fall back cleanly to CPU
            tr = None
        if tr is None:
            import os

            cpus = os.cpu_count() or 4
            inter = 2 if cpus >= 6 else 1
            intra = min(8, max(2, cpus // (2 * inter)))
            try:
                tr = ctranslate2.Translator(str(ct2_dir), device="cpu", intra_threads=intra, inter_threads=inter)
            except TypeError:
                tr = ctranslate2.Translator(str(ct2_dir), device="cpu")
        entry = (tr, sp_src, sp_tgt)
        with _CT2_CACHE_LOCK:
            _CT2_CACHE[key] = (entry, est_bytes)
            _CT2_CACHE.move_to_end(key)
            total_bytes = sum(b for _, b in _CT2_CACHE.values())
            while len(_CT2_CACHE) > 1 and (len(_CT2_CACHE) > _CT2_CACHE_MAX or total_bytes > _CT2_CACHE_MAX_BYTES):
                _, (_, evicted_bytes) = _CT2_CACHE.popitem(last=False)
                total_bytes -= evicted_bytes
            _CT2_KEY_LOCKS.pop(key, None)
        return entry


def cached_models_info():
    """Return count and estimated RAM bytes of currently cached CTranslate2 models."""
    with _CT2_CACHE_LOCK:
        return {
            "count": len(_CT2_CACHE),
            "bytes": sum(b for _, b in _CT2_CACHE.values()),
        }


def unload_cached_models():
    """Release all cached CTranslate2 translators from RAM and run GC."""
    import gc

    with _CT2_CACHE_LOCK:
        count = len(_CT2_CACHE)
        freed = sum(b for _, b in _CT2_CACHE.values())
        _CT2_CACHE.clear()
        _CT2_KEY_LOCKS.clear()
    gc.collect()
    return {"unloaded": count, "freed_bytes": freed}


_ZH_TRAD_CHARS = set("這為們個說會時國學體點對關開門車書長發經與從進還實務業")
_ZH_SIMP_CHARS = set("这为们个说会时国学体点对关开门车书长发经与从进还实务业")
_LATIN_STOPWORDS = {
    "en": {"the", "and", "of", "to", "in", "is", "for", "that", "with", "this", "are", "from", "will", "not", "be"},
    "es": {"el", "la", "los", "las", "de", "del", "que", "en", "para", "con", "por", "una", "como", "este", "está"},
    "fr": {"le", "la", "les", "des", "du", "de", "et", "est", "dans", "pour", "avec", "une", "sur", "pas", "nous"},
    "de": {"der", "die", "das", "und", "den", "von", "zu", "mit", "ist", "für", "auf", "nicht", "eine", "dem", "sich"},
    "pt": {"os", "as", "dos", "das", "que", "com", "para", "uma", "por", "não", "são", "como", "mais", "este", "pela"},
    "it": {"il", "la", "gli", "delle", "della", "che", "con", "per", "una", "sono", "nel", "anche", "questo", "non"},
    "nl": {"de", "het", "een", "van", "en", "in", "op", "voor", "met", "zijn", "niet", "aan", "ook", "dat", "worden"},
    "pl": {"się", "jest", "nie", "oraz", "przez", "jako", "który", "tego", "tym", "dla", "będzie", "może", "tylko"},
    "tr": {"ve", "bir", "bu", "için", "ile", "olan", "olarak", "daha", "çok", "gibi", "kadar", "tarafından", "ancak"},
    "vi": {"của", "và", "các", "trong", "được", "cho", "với", "không", "này", "một", "những", "người", "từ", "đến"},
}


def detect_language(text):
    """Offline zero-dependency language detector returning a ZPDF BCP-47 code
    (e.g. ``en``, ``zh-Hans``, ``zh-Hant``, ``ja``, ``ko``, ``es``, ``fr``, ``de``)."""
    sample = str(text or "")[:6000]
    if not sample.strip():
        return "en"
    # 1. Script-based detection for non-Latin scripts
    ja_kana = len(re.findall(r"[\u3040-\u30ff]", sample))
    ko_hangul = len(re.findall(r"[\uac00-\ud7af]", sample))
    han_chars = re.findall(r"[\u4e00-\u9fff]", sample)
    cyrillic = re.findall(r"[\u0400-\u04ff]", sample)
    arabic = len(re.findall(r"[\u0600-\u06ff]", sample))
    hebrew = len(re.findall(r"[\u0590-\u05ff]", sample))
    devanagari = len(re.findall(r"[\u0900-\u097f]", sample))
    thai = len(re.findall(r"[\u0e00-\u0e7f]", sample))
    greek = len(re.findall(r"[\u0370-\u03ff]", sample))

    if ja_kana >= 3:
        return "ja"
    if ko_hangul >= 3:
        return "ko"
    if len(han_chars) >= 3:
        trad = sum(1 for ch in han_chars if ch in _ZH_TRAD_CHARS)
        simp = sum(1 for ch in han_chars if ch in _ZH_SIMP_CHARS)
        return "zh-Hant" if trad > simp else "zh-Hans"
    if len(cyrillic) >= 6:
        uk_hits = sum(1 for ch in cyrillic if ch.lower() in "іїєґ")
        return "uk" if uk_hits >= 2 else "ru"
    if arabic >= 6:
        return "ar"
    if hebrew >= 6:
        return "he"
    if devanagari >= 6:
        return "hi"
    if thai >= 6:
        return "th"
    if greek >= 6:
        return "el"

    # 2. Latin-script stopword + diacritic scoring
    words = re.findall(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]{2,}", sample.lower())
    if not words:
        return "en"
    scores = {lang: sum(1 for w in words if w in sw) for lang, sw in _LATIN_STOPWORDS.items()}
    # Boost distinctive diacritics
    if re.search(r"[ñ¡¿]", sample.lower()):
        scores["es"] += 3
    if re.search(r"[ßäöü]", sample.lower()):
        scores["de"] += 3
    if re.search(r"[çœæ]", sample.lower()):
        scores["fr"] += 2
    if re.search(r"[ãõ]", sample.lower()):
        scores["pt"] += 3
    if re.search(r"[łńśźżąćę]", sample.lower()):
        scores["pl"] += 4
    if re.search(r"[ğışçöü]", sample.lower()):
        scores["tr"] += 3
    if re.search(r"[ăâđêôơư]", sample.lower()):
        scores["vi"] += 4
    best_lang, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_lang if best_score >= 2 else "en"
