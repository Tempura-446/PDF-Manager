# P4 — Backend Modularization (Wave 2): Implementation Plan

> **For agentic workers:** Follow the `superpowers` skill. Execute phase-by-phase on a feature branch, running targeted tests + `tests/test_imports.py` + `tests/test_headers.py` after every phase before committing. Track progress with `- [ ]` checkboxes.

**Goal:** Split the 6 remaining >2,000-LOC backend monoliths into cohesive subpackages and replace all 44 silent `except Exception: pass` sites with typed handling + `applog` diagnostics for **`v0.65.0`**.

**Spec:** [`docs/superpowers/specs/2026-11-24-backend-modularization-wave2-design.md`](../specs/2026-11-24-backend-modularization-wave2-design.md)

## Global Constraints
- **Zero behavior changes** and **100% public API compatibility** via package `__init__.py` re-exports.
- **License header** on every new `.py` file (`tests/test_headers.py`).
- **No new circular imports** (`tests/test_imports.py`).
- **Preserve cold startup speed** (`import pdfmanager.gui` must not eagerly load `pypdf`/`PIL`/`pikepdf`/`numpy`/`cryptography`).

---

### Phase 1: Split `pdfmanager/accessibility.py` (2,901 LOC, 0 monkeypatches)
- [ ] Create `pdfmanager/accessibility/` package:
  - `geometry.py` — constants/regexes, box/matrix math, `_View`, `_inherited`
  - `scanner.py` — `_Font`, `_standard_font`, `_Unit`, `_Scanner`, form/XObject inspection, `_rewrite`/`_strip_marks`/`_strip_xobjects`
  - `layout.py` — `_Seg`, `_page_segments`, furniture/language (`guess_language`), XY-cut `_reading_order`, `_Line`/`_Block`/`_text_blocks`, table grids
  - `tagger.py` — `_Elem`, `_Targets`, `_PageTagger`, headings, `_tag_pages`, `_write_tree`, `autotag`, `tag_summary`, doc helpers
  - `checker.py` — `RULES`, `MANUAL_NOTE`, `_Found`, `_TreeFacts`, `_content_marks`, `_font_unmapped`, `check`, `summary`, `report`
  - `__init__.py` — re-export public API
- [ ] Verify: `pytest -q tests/test_accessibility*.py tests/test_imports.py tests/test_headers.py`

### Phase 2: Split `pdfmanager/server/operations.py` (2,125 LOC, 0 direct monkeypatches)
- [ ] Create `pdfmanager/server/operations/` package:
  - `catalog.py` — `_TRANSLATE_LANGS`, `PAPER`, `OPERATIONS`, `OPS`, `SUFFIX`, `default_options`
  - `dispatch.py` — `_run_file_op` decomposed into clean domain handlers + `_form_op`, `_sign_cert_op`, `_to_excel`, `_redact_*`
  - `repair.py` — `_protect`, `_was_protected`, `_flatten_outputs`, `_damaged_errors`, `_plainly`, `_rebuildable`, `_tidy`, `_needs_mending`, `_mend_inputs`, `_check_pages`
  - `runner.py` — `run_operation`, `_run_operation`, `_run_files`, `run_action`, `_start_action`, `run_folder_operation`
  - `preview.py` — `THUMB_WIDTH`, `PREVIEW_OPS`, `PREVIEW_PAGES`, `preview_operation`, `_preview_operation`, `inspect_pdf`, `_inspect_pdf`
  - `__init__.py` — re-export full API (preserving `gui.run_operation` re-export)
- [ ] Verify: `pytest -q tests/test_v064_coverage.py tests/test_audit_server.py tests/test_run_opened.py tests/test_imports.py tests/test_headers.py`

### Phase 3: Split `pdfmanager/cli.py` (2,426 LOC, 0 monkeypatches)
- [ ] Create `pdfmanager/cli/` package:
  - `common.py` — `_default_output`, `_human_size`, `_print_plan`, `_expand`, `_Passwords`, `_ask_new_password`, `OUTPUTS`, `KEEP_UNENCRYPTED`, `run_per_file`
  - `commands_edit.py` — per-file page/security/stamp/ocr/archive/cert/accessibility/translate commands
  - `commands_convert.py` — per-file bookmarks/forms/office/dxf/sheetsets/comments/redact/info commands
  - `commands_multi.py` — multi-input, scan, folder, recipe, watch, setup-windows, gui commands
  - `parser.py` — `_positive`, `build_parser` (split by command group), `_leading_dash_ranges`
  - `__init__.py` — `main`, `build_parser`, and public re-exports
- [ ] Verify: `pytest -q tests/test_cli*.py tests/test_audit_cli.py tests/test_imports.py tests/test_headers.py`

### Phase 4: Split `pdfmanager/translate.py` (2,658 LOC, 4 monkeypatches)
- [ ] Create `pdfmanager/translate/` package:
  - `models.py` — pair discovery, `installed_translation_pairs`, `model_dir_for`, `_SubwordBpe`, `_load_tokenizer`, CT2 cache, `detect_language`
  - `engines.py` — `_translate_ct2_bucketed`, CJK cleanup, MADLAD codes/loader, LLM parser/loader, `_load_offline_translator`
  - `text.py` — `protect_tokens`/`restore_tokens`, `split_sentences`, glossary, neural entity protection, `translate_texts`, `translate_page`
  - `layout.py` — text/bilingual page builders, running-margin filter, annotations/outlines, RTL shaping, `_extract_layout_blocks`, `_strip_text_from_pdf`, `_make_layout_overlay`, `_render_page_bg`
  - `pipeline.py` — `translate_pdf`
  - `__init__.py` — re-exports + facade lookups for `_load_offline_translator`, `_load_single_translator`, `_load_llm_translator`
- [ ] Verify: `pytest -q tests/test_translate*.py tests/test_audit_remediation.py tests/test_imports.py tests/test_headers.py`

### Phase 5: Split `pdfmanager/core.py` (2,276 LOC, 18 monkeypatches)
- [ ] Create `pdfmanager/core/` package:
  - `io.py` — `PAPER_SIZES`, `open_pdf`, `open_pikepdf`, `keep_size`, `_write`, `clone_document`, `_recount`, `_own_contents`, `info`, `extract_text`, `batch_run`, `default_workers`
  - `pages.py` — `merge`, `split`, `outline_items`, `_size_chunks`, `_pages_bytes`, `select_pages`, `delete_pages`, `reverse`, `interleave`
  - `geometry.py` — `parse_paper_size`, `_transform_annotations`, `arrange`, `rotate`, `resize`, `quarter_turn`, `crop`, `n_up`, `booklet`, `_retarget_links`, `insert_blank_pages`, `trim_margins`
  - `security.py` — `compress`, `pack`, `convert_color`, `encrypt`, `decrypt`, `set_metadata`, `_update_xmp`
  - `stamp.py` — `pdf_canvas`, `watermark`, `number_pages`, `extract_media`, `headers_footers`
  - `convert.py` — `images_to_pdf`, `ocr`, `PDFIUM_LOCK`, `MAX_RENDER_PIXELS`, `render_scale`, `show_whole_page`, `pdf_to_images`
  - `__init__.py` — re-exports + facade lookups for `_pages_bytes`, `MAX_RENDER_PIXELS`, `render_scale`, `ocr`, `merge`, `rotate`
- [ ] Update `tests/test_imports.py` to cover subpackages cleanly.
- [ ] Verify: `pytest -q tests/test_pagetools.py tests/test_sweep_fixes.py tests/test_features.py tests/test_imports.py tests/test_headers.py`

### Phase 6: Split `pdfmanager/ocr.py` (2,148 LOC, 69 monkeypatches)
- [ ] Create `pdfmanager/ocr/` package:
  - `engines.py` — constants, `Line`, script/language maps, availability checks
  - `models.py` — GPU/quality defaults, `models_dir`, `_model_source`, `_best_model_source`, `_fetch`, `_download_model`, `pack_ocr_model`, `_engine`, `reset`
  - `preprocess.py` — image measurement, flattening, photo straightening, line dewarping, `_retry_low_confidence`, `_read`, `recognise`
  - `layout.py` — `_lines`/`_words`, XY-cut, `reading_order`, `text_of`, `skew_angle`, `page_orientation`, text-layer/picture heuristics, `format_report`
  - `runner.py` — `run`, `_run_tesseract`, `_tesseract_languages`, `default_workers`, `_available_memory_mb`, worker pool, `_run_builtin`, `_read_pages`, `_rasterise`
  - `__init__.py` — full re-exports + facade lookups (`sys.modules["pdfmanager.ocr"]`) preserving all 69 test monkeypatch points and `_ORIG_READ`/`_ORIG_RECOGNISE` detection
- [ ] Verify: `pytest -q tests/test_ocr*.py tests/test_imports.py tests/test_headers.py`

### Phase 7: Replace All 44 Silent `except Exception: pass` Sites + Release Prep (`v0.65.0`)
- [ ] Replace all 44 silent `except Exception: pass` blocks across `pdfmanager/` with typed exceptions and/or `applog.debug` / `applog.warning` diagnostics.
- [ ] Add a lint/quality check in `tests/test_code_quality.py` (or targeted test) preventing new bare `except Exception: pass` regressions.
- [ ] Update `docs/ROADMAP.md` (mark `v0.64.1`–`v0.64.4` and P4 `v0.65.0` done), bump version to `0.65.0`, sync `CHANGELOG.md` + `packaging/` manifests.
- [ ] Full verification: `pytest -q`, cold-startup import check, PR + CI green + release `v0.65.0`.
