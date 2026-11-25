# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Token/entity protection, glossary rules, sentence chunking, and text/page translation API."""

import re
import sys
from pathlib import Path

from pdfmanager import core, i18n, progress
from pdfmanager.translate.engines import (
    _extract_paragraphs, _is_cjk_fullwidth_lang, _is_cjk_lang, _load_offline_translator,
)
from pdfmanager.translate.models import detect_language

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
MODES = ("side-by-side", "bilingual-pages", "bilingual", "in-place", "manga", "text")


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


# Entities protected during neural translation using collision-free numeric sentinels
# (URLs, emails, phone numbers, reference codes, ISO dates, currency & formatted numbers).
# Plain small integers / years (e.g. "December 31, 2026", "3 items") are left unmasked so
# the neural model translates dates and counts into natural target-language grammar.
_NEURAL_PROTECT_RE = re.compile(
    r"(?:"
    r"`[^`\n]{1,80}`"
    r"|https?://[^\s<>()\"']+[^\s<>()\"'.,;:!?]"
    r"|www\.[^\s<>()\"']+[^\s<>()\"'.,;:!?]"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}"
    r"|\b[A-Z]{2,}(?:-[A-Z0-9]+){1,4}\b"
    r"|\bv\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9.]+)?\b"
    r"|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|(?:[$€£¥￥]\s?\d[\d,]*(?:\.\d+)?)"
    r"|\b(?:USD|EUR|GBP|JPY|CHF|CAD|AUD|CNY|KRW)\s?\d[\d,]*(?:\.\d+)?\b"
    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|JPY|CHF|CAD|AUD|CNY|KRW)\b"
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
    so context-capable models (MADLAD-400, LLM) translate with paragraph context."""
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
    """Restore numeric sentinels in ``translated``; returns ``(text, all_found)``.

    Tolerates MT engines inserting thousands separators (``,``, ``.``, spaces)
    or converting ASCII digits to fullwidth digits (``０-９``).
    """
    out = translated
    all_found = True
    for sentinel, original in mapping:
        digit_pats = []
        for ch in sentinel:
            if ch.isdigit():
                fw = chr(ord("０") + int(ch))
                digit_pats.append(f"[{ch}{fw}]")
            else:
                digit_pats.append(re.escape(ch))
        pattern = re.compile(r"[\s,.\u00a0\u202f]*".join(digit_pats))
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


def _split_long_for_mt(text, max_chars=450):
    """Split overly long paragraphs along sentence boundaries so 512-token
    offline neural MT models never silently truncate the tail."""
    s = str(text or "").strip()
    if len(s) <= max_chars:
        return [s] if s else []
    sents = split_sentences(s) or [s]
    chunks = []
    cur = ""
    for sent in sents:
        if len(sent) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            words = sent.split()
            part = ""
            for w in words:
                cand = (part + " " + w).strip() if part else w
                if len(cand) <= max_chars:
                    part = cand
                else:
                    if part:
                        chunks.append(part)
                    part = w
            if part:
                chunks.append(part)
        elif not cur:
            cur = sent
        elif len(cur) + 1 + len(sent) <= max_chars:
            cur = cur + " " + sent
        else:
            chunks.append(cur)
            cur = sent
    if cur:
        chunks.append(cur)
    return chunks


def translate_texts(texts, src_lang, dst_lang, *, translator=None, batch_size=32, glossary=None):
    """Translate a sequence of strings offline, preserving emails, URLs, phone
    numbers, reference codes, dates, currency, bullets, key-value labels, and
    optional user glossary terms, while deduplicating identical chunks."""
    _loader = getattr(sys.modules.get("pdfmanager.translate"), "_load_offline_translator", _load_offline_translator)
    fn = translator if translator is not None else _loader(src_lang, dst_lang)
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
    eff_batch = max(1, int(batch_size))
    if is_neural and not is_llm and eff_batch < 128:
        # CTranslate2 engines internally sort by token length and slice into
        # _CT2_SUB_BATCH chunks with mid-batch progress.check(); feeding 128
        # unique sentences per call minimizes padding waste across book pages.
        eff_batch = 128
    for start in range(0, total, eff_batch):
        progress.check()
        progress.report(start, max(1, total), "translating text")
        chunk = unique_sentences[start : start + eff_batch]
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
    resolved_src = detect_language(raw_text) if str(src_lang or "").strip().lower() in ("", "auto") else i18n.normalize_code(src_lang)
    if not paragraphs:
        return {"page": page_num, "src_lang": resolved_src, "dst_lang": i18n.normalize_code(dst_lang), "original": "", "translated": ""}
    translated_paras = translate_texts(paragraphs, resolved_src, dst_lang, translator=translator)
    return {
        "page": page_num,
        "src_lang": resolved_src,
        "dst_lang": i18n.normalize_code(dst_lang),
        "original": "\n\n".join(paragraphs),
        "translated": "\n\n".join(translated_paras),
    }
