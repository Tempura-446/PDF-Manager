# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Offline PDF translation engine and output modes (in-place, side-by-side, text).

Uses offline translation models from installed language packs
(``<data_dir>/locales/<code>/models/<src>-<dst>/`` via CTranslate2 or Argos
Translate when installed), or a caller-supplied ``translator`` function.
Numbers, reference codes (e.g. ``INV-2026-118``), URLs, and emails are
protected with placeholders so they pass through unchanged.
"""

from pdfmanager.translate.models import (
    _CT2_CACHE, _CT2_CACHE_LOCK, _CT2_CACHE_MAX, _CT2_CACHE_MAX_BYTES,
    _CT2_KEY_LOCKS, _SubwordBpe, _get_cached_ct2, _load_tokenizer,
    _pair_candidates, cached_models_info, clear_model_cache, detect_language,
    ensure_runtime, installed_translation_pairs, model_dir_for, unload_cached_models,
)
from pdfmanager.translate.engines import (
    _APPROX_PACK_MB, _CT2_SUB_BATCH, _MADLAD_CODES, _clean_mt_output,
    _extract_paragraphs, _is_cjk_fullwidth_lang, _is_cjk_lang,
    _load_llm_translator, _load_madlad_translator, _load_offline_translator,
    _load_single_translator, _parse_llm_json_list, _translate_ct2_bucketed,
    bcp47_to_madlad, madlad_model_dir,
)
from pdfmanager.translate.text import (
    MODES, _PROTECT_RE, _chunk_sentences_for_context, _compile_glossary_patterns,
    _find_protected_spans, _is_verbatim_block, _normalize_all_caps,
    _protect_neural_entities, _restore_neural_entities, _split_structural_affixes,
    _translate_around_entities, parse_glossary, protect_tokens, restore_tokens,
    split_sentences, translate_page, translate_texts,
)
from pdfmanager.translate.layout import (
    _detect_align, _detect_bold_prefix, _detect_font_style, _detect_rotation,
    _detect_running_margin_texts, _extract_layout_blocks, _is_code_or_math_block,
    _is_running_margin_block, _luminance, _make_bilingual_pages, _make_layout_overlay,
    _make_text_page, _normalize_color, _page_crop_origin, _render_page_bg,
    _sample_bg_rgb, _shape_rtl, _strip_bt_et_ops, _strip_text_from_pdf,
    _translate_annotations_and_fields, _translate_outlines, _wrap_text,
)
from pdfmanager.translate.vertical import (
    extract_vertical_cjk_blocks, is_vertical_cjk_page, normalize_vertical_punct,
)
from pdfmanager.translate.manga import (
    detect_speech_bubbles, extract_bubble_text, make_manga_overlay, order_manga_bubbles,
)
from pdfmanager.translate.pipeline import translate_pdf
