# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""PDF → Markdown (.md), offline.

Builds on ``webpage.flow``'s column-aware reading order, running header/footer
removal, heading/list classification, and ``tables.py`` table extraction so
the Markdown matches ZPDF's flowing HTML structure cleanly.
"""

import re
from pathlib import Path

from pdfmanager import core, layout, webpage

_BULLET_OR_NUM = layout.LIST_ITEM


def _yaml_quote(value):
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{text}"'


def _escape_table_cell(text):
    text = " ".join((text or "").replace("\r", "\n").split("\n")).strip()
    return text.replace("|", "\\|")


def _wrap_code(text):
    if "`" not in text:
        return f"`{text}`"
    fence = "``"
    while fence in text:
        fence += "`"
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _format_run(text, bold, italic, mono, heading=False):
    if not text:
        return ""
    lead = len(text) - len(text.lstrip(" "))
    trail = len(text) - len(text.rstrip(" "))
    core_text = text[lead: len(text) - trail if trail else len(text)]
    if not core_text:
        return text
    if mono:
        styled = _wrap_code(core_text)
    else:
        use_bold = bold and not heading
        if use_bold and italic:
            styled = f"***{core_text}***"
        elif use_bold:
            styled = f"**{core_text}**"
        elif italic:
            styled = f"*{core_text}*"
        else:
            styled = core_text
    return (" " * lead) + styled + (" " * trail)


_MONO_HINTS = ("courier", "mono", "consolas", "menlo", "inconsolata", "code", "fixed")


def _is_mono(ch):
    fam = (getattr(ch, "family", None) or "").lower()
    return any(hint in fam for hint in _MONO_HINTS)


def _format_segments(segments, links=None, heading=False):
    """Render ``[(text, layout.Char)]`` to inline Markdown, grouping adjacent
    segments with the same style or link target."""
    if not segments:
        return ""
    links = links or ()

    # Group contiguous segments by link target first so a multi-style link
    # becomes one [styled text](url) rather than separate links per word.
    link_groups = []
    for text, ch in segments:
        if not text:
            continue
        idx = getattr(ch, "link", None)
        uri = None
        if idx is not None and 0 <= idx < len(links):
            uri = links[idx].get("uri")
        if link_groups and link_groups[-1][0] == uri:
            link_groups[-1][1].append((text, ch))
        else:
            link_groups.append((uri, [(text, ch)]))

    parts = []
    for uri, group in link_groups:
        # Merge runs with identical (bold, italic, mono) inside the group.
        runs = []
        for text, ch in group:
            style = (bool(ch.bold), bool(ch.italic), _is_mono(ch))
            if runs and runs[-1][1] == style:
                runs[-1][0] += text
            else:
                runs.append([text, style])
        rendered = "".join(_format_run(t, *style, heading=heading) for t, style in runs)
        if uri and rendered.strip():
            lead = len(rendered) - len(rendered.lstrip(" "))
            trail = len(rendered) - len(rendered.rstrip(" "))
            inner = rendered[lead: len(rendered) - trail if trail else len(rendered)]
            rendered = (" " * lead) + f"[{inner}]({uri})" + (" " * trail)
        parts.append(rendered)

    text = "".join(parts)
    # Collapse accidental double spaces outside code spans.
    return re.sub(r"[ \t]+", " ", text).strip()


def _is_code_block(segments):
    non_space = [(t, c) for t, c in segments if t.strip()]
    if not non_space:
        return False
    return all(_is_mono(c) for _, c in non_space) and len("".join(t for t, _ in non_space)) >= 12


def _format_list_item(segments, links):
    raw = "".join(t for t, _ in segments).lstrip()
    m = _BULLET_OR_NUM.match(raw)
    if not m:
        return "ul", f"- {_format_segments(segments, links)}"
    # Strip the marker prefix from segments while preserving inline styles.
    leading_ws = len("".join(t for t, _ in segments)) - len(raw)
    body_segments = webpage._drop_marker(segments, leading_ws + m.end())
    body = _format_segments(body_segments, links)
    if not body:
        return None, ""
    ordered = m.group(1)
    if ordered and ordered.isdigit():
        return "ol", f"{int(ordered)}. {body}"
    if ordered:
        return "ol", f"1. {body}"
    return "ul", f"- {body}"


def _format_table(rows):
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    if cols == 0:
        return ""

    def pad(row):
        cells = [_escape_table_cell(c) for c in row]
        if len(cells) < cols:
            cells.extend([""] * (cols - len(cells)))
        return "| " + " | ".join(cells) + " |"

    out = [pad(rows[0]), "| " + " | ".join(["---"] * cols) + " |"]
    for row in rows[1:]:
        out.append(pad(row))
    return "\n".join(out)


def _front_matter_block(path, password, title, page_count):
    reader = core.open_pdf(path, password)
    meta = reader.metadata or {}
    doc_title = (meta.get("/Title") or "").strip() or title
    author = (meta.get("/Author") or "").strip()
    subject = (meta.get("/Subject") or "").strip()
    lines = ["---", f"title: {_yaml_quote(doc_title)}"]
    if author:
        lines.append(f"author: {_yaml_quote(author)}")
    if subject:
        lines.append(f"subject: {_yaml_quote(subject)}")
    lines.append(f"pages: {page_count}")
    lines.append(f"source: {_yaml_quote(Path(path).name)}")
    lines.append("---")
    return "\n".join(lines)


def _render_markdown(path, pages_items, title, password=None, output=None,
                     extract_images=False, page_breaks=False, front_matter=False):
    blocks = []
    stats = {"pages": len(pages_items), "headings": 0, "tables": 0, "images": 0,
             "blocks": 0, "assets_dir": None}

    if front_matter:
        blocks.append(_front_matter_block(path, password, title, len(pages_items)))

    out_path = Path(output) if output is not None else None
    assets_dir = (out_path.parent / f"{out_path.stem}_assets") if (extract_images and out_path) else None
    fig_num = 0

    for page_idx, items in enumerate(pages_items, start=1):
        page_blocks = []
        list_buf = []
        list_kind = None
        img_on_page = 0

        def flush_list():
            nonlocal list_kind
            if list_buf:
                page_blocks.append("\n".join(list_buf))
                list_buf.clear()
            list_kind = None

        for item in items:
            kind = item["kind"]
            if kind == "heading":
                flush_list()
                level = max(1, min(6, int(item.get("level") or 1)))
                text = _format_segments(item["segments"], item.get("links"), heading=True)
                if text:
                    page_blocks.append(f"{'#' * level} {text}")
                    stats["headings"] += 1
                    stats["blocks"] += 1
            elif kind == "item":
                lkind, line = _format_list_item(item["segments"], item.get("links"))
                if line:
                    if list_kind and list_kind != lkind:
                        flush_list()
                    list_kind = lkind
                    list_buf.append(line)
                    stats["blocks"] += 1
            elif kind == "paragraph":
                flush_list()
                segs = item["segments"]
                if _is_code_block(segs):
                    code = "".join(t for t, _ in segs).strip()
                    if code:
                        page_blocks.append(f"```\n{code}\n```")
                        stats["blocks"] += 1
                else:
                    text = _format_segments(segs, item.get("links"))
                    if text:
                        page_blocks.append(text)
                        stats["blocks"] += 1
            elif kind == "table":
                flush_list()
                tbl = _format_table(item.get("rows") or [])
                if tbl:
                    page_blocks.append(tbl)
                    stats["tables"] += 1
                    stats["blocks"] += 1
            elif kind == "image" and extract_images and assets_dir is not None:
                flush_list()
                img = item.get("image")
                if img is not None:
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    img_on_page += 1
                    fig_num += 1
                    fname = f"page{page_idx}_img{img_on_page}.png"
                    img.save(assets_dir / fname, format="PNG")
                    rel = f"{assets_dir.name}/{fname}"
                    page_blocks.append(f"![Figure {fig_num}]({rel})")
                    stats["images"] += 1
                    stats["blocks"] += 1
                    stats["assets_dir"] = str(assets_dir)

        flush_list()
        if page_blocks:
            if page_breaks and page_idx > 1 and blocks:
                blocks.append(f"<!-- Page {page_idx} -->\n\n---")
            blocks.extend(page_blocks)

    text = "\n\n".join(blocks) + ("\n" if blocks else "")
    stats["textless"] = stats["blocks"] == 0
    return text, stats


def to_markdown_text(path, password=None, pages=None, page_breaks=False, front_matter=False):
    """Convert a PDF to a Markdown string without writing to disk."""
    title, pages_items = webpage.flow(path, password=password, pages=pages)
    text, _ = _render_markdown(path, pages_items, title, password=password,
                               extract_images=False, page_breaks=page_breaks,
                               front_matter=front_matter)
    return text


def pdf_to_markdown(path, output, password=None, pages=None, extract_images=False,
                    page_breaks=False, front_matter=False):
    """Convert ``path`` to a Markdown file at ``output`` (UTF-8).

    When ``extract_images`` is True, page pictures are saved as PNGs in
    ``<output_stem>_assets/`` beside ``output`` and referenced with relative
    Markdown image links.
    """
    out = Path(output)
    title, pages_items = webpage.flow(path, password=password, pages=pages)
    text, stats = _render_markdown(path, pages_items, title, password=password,
                                   output=out, extract_images=extract_images,
                                   page_breaks=page_breaks, front_matter=front_matter)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    stats["output"] = str(out)
    return stats
