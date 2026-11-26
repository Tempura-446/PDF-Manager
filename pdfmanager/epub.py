# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Pure-Python EPUB e-book to PDF converter for direct ZPDF Reader opening.

Parses EPUB 2/3 container.xml + OPF spine + XHTML chapters + embedded images
using only Python stdlib (zipfile, xml.etree.ElementTree, html.parser) + Pillow
+ ReportLab + pypdf, with clickable chapter outline bookmarks and RTL detection.
"""

import io
import logging
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from pdfmanager import core, fonts, progress

MAX_EPUB_ENTRIES = 4000
MAX_EPUB_UNCOMPRESSED_BYTES = 500_000_000  # 500 MB safety cap


def _safe_zip_name(name):
    norm = str(name or "").replace("\\", "/")
    if norm.startswith("/") or norm.startswith("~") or re.match(r"^[A-Za-z]:", norm):
        raise core.PdfManagerError(f"EPUB archive contains unsafe path: {name!r}")
    if any(part == ".." for part in norm.split("/")):
        raise core.PdfManagerError(f"EPUB archive contains unsafe path: {name!r}")
    return norm


def _local_tag(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


class _ChapterParser(HTMLParser):
    """Extract ordered blocks (headings, paragraphs, list items, images) from XHTML."""

    BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "div"}
    SKIP_TAGS = {"script", "style", "head", "nav"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._skip_depth = 0
        self._cur_tag = None
        self._buf = []
        self.title = ""
        self._in_title = False

    def _flush(self):
        if not self._buf:
            self._cur_tag = None
            return
        text = " ".join("".join(self._buf).split())
        if text:
            tag = self._cur_tag or "p"
            self.blocks.append((tag, text))
        self._buf = []
        self._cur_tag = None

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = True
            return
        if t == "img":
            self._flush()
            attr_map = dict(attrs)
            src = attr_map.get("src") or ""
            if src:
                self.blocks.append(("img", src))
            return
        if t == "br":
            self._buf.append("\n")
            return
        if t in self.BLOCK_TAGS:
            self._flush()
            self._cur_tag = t

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = False
            return
        if t in self.BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        self._buf.append(data)


def _find_opf_path(zf):
    """Locate the OPF package file via META-INF/container.xml or fallback scan."""
    names = {info.filename for info in zf.infolist() if not info.is_dir()}
    for cand in ("META-INF/container.xml", "meta-inf/container.xml"):
        if cand in names:
            try:
                root = ET.fromstring(zf.read(cand))
                for elem in root.iter():
                    if _local_tag(elem.tag) == "rootfile":
                        full_path = elem.attrib.get("full-path") or ""
                        if full_path and full_path in names:
                            return full_path
            except Exception as exc:
                logging.getLogger("zpdf").debug("epub.container.xml: %s", exc)
    for name in sorted(names):
        if name.lower().endswith(".opf"):
            return name
    return None


def epub_to_pdf(epub_path, output_pdf):
    """Convert an ``.epub`` e-book into a cleanly typeset PDF with chapter bookmarks."""
    from PIL import Image as PilImage
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DictionaryObject, NameObject
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Image as RlImage, PageBreak, Paragraph, SimpleDocTemplate, Spacer

    src = Path(epub_path)
    dst = Path(output_pdf)
    if not src.is_file():
        raise core.PdfManagerError(f"EPUB file not found: {src}")

    try:
        zf = zipfile.ZipFile(src, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise core.PdfManagerError(f"could not open EPUB archive: {exc}") from None

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_EPUB_ENTRIES:
            raise core.PdfManagerError(f"EPUB archive has too many entries ({len(infos)} > {MAX_EPUB_ENTRIES})")
        total_bytes = 0
        for info in infos:
            _safe_zip_name(info.filename)
            total_bytes += int(info.file_size or 0)
            if total_bytes > MAX_EPUB_UNCOMPRESSED_BYTES:
                raise core.PdfManagerError("EPUB archive exceeds safe uncompressed size limit")

        opf_path = _find_opf_path(zf)
        book_title = src.stem
        rtl = False
        spine_paths = []
        name_set = {info.filename for info in infos if not info.is_dir()}

        if opf_path:
            opf_dir = posixpath.dirname(opf_path)
            try:
                opf_root = ET.fromstring(zf.read(opf_path))
                manifest = {}
                for elem in opf_root.iter():
                    lt = _local_tag(elem.tag)
                    if lt == "title" and (elem.text or "").strip() and book_title == src.stem:
                        book_title = elem.text.strip()
                    elif lt == "item":
                        iid = elem.attrib.get("id")
                        href = elem.attrib.get("href")
                        if iid and href:
                            joined = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
                            manifest[iid] = joined
                    elif lt == "spine":
                        if (elem.attrib.get("page-progression-direction") or "").lower() == "rtl":
                            rtl = True
                        for child in elem:
                            if _local_tag(child.tag) == "itemref":
                                idref = child.attrib.get("idref")
                                if idref and idref in manifest and manifest[idref] in name_set:
                                    spine_paths.append(manifest[idref])
            except Exception as exc:
                logging.getLogger("zpdf").debug("epub.opf: %s", exc)

        if not spine_paths:
            spine_paths = sorted(
                n for n in name_set
                if n.lower().endswith((".xhtml", ".html", ".htm")) and "__MACOSX" not in n.split("/")
            )
        if not spine_paths:
            raise core.PdfManagerError("EPUB contains no readable chapters")

        page_w, page_h = LETTER
        margin = 54.0
        max_img_w = page_w - 2 * margin
        max_img_h = page_h - 2 * margin - 40.0

        story = []
        chapter_bookmarks = []  # (title, flowable_index)
        page_marks = {}

        class _BookmarkDoc(SimpleDocTemplate):
            def afterFlowable(self, flowable):
                label = getattr(flowable, "_epub_chapter", None)
                if label:
                    page_marks.setdefault(label, self.page)

        buf = io.BytesIO()
        doc = _BookmarkDoc(
            buf,
            pagesize=LETTER,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin,
            bottomMargin=margin,
            title=book_title,
        )

        total = len(spine_paths)
        for idx, ch_path in enumerate(spine_paths):
            progress.check()
            progress.report(idx, total, "converting EPUB chapter")
            try:
                raw_html = zf.read(ch_path).decode("utf-8", errors="replace")
            except Exception:
                continue
            parser = _ChapterParser()
            parser.feed(raw_html)
            parser._flush()
            if not parser.blocks:
                continue
            if idx > 0 and story:
                story.append(PageBreak())

            ch_dir = posixpath.dirname(ch_path)
            ch_title = (parser.title or "").strip() or f"Chapter {idx + 1}"
            marked_chapter = False

            for tag, content in parser.blocks:
                if tag == "img":
                    img_rel = posixpath.normpath(posixpath.join(ch_dir, content)) if ch_dir else content
                    if img_rel not in name_set:
                        continue
                    try:
                        img_raw = zf.read(img_rel)
                        with PilImage.open(io.BytesIO(img_raw)) as pil:
                            iw, ih = pil.size
                            out_buf = io.BytesIO()
                            pil.convert("RGB").save(out_buf, format="JPEG", quality=88)
                            out_buf.seek(0)
                        scale = min(1.0, max_img_w / max(1.0, float(iw)), max_img_h / max(1.0, float(ih)))
                        rl_img = RlImage(out_buf, width=iw * scale, height=ih * scale)
                        if not marked_chapter:
                            rl_img._epub_chapter = ch_title
                            chapter_bookmarks.append(ch_title)
                            marked_chapter = True
                        story.append(rl_img)
                        story.append(Spacer(1, 8))
                    except Exception as exc:
                        logging.getLogger("zpdf").debug("epub.img %s: %s", img_rel, exc)
                    continue

                is_heading = tag in ("h1", "h2", "h3", "h4")
                if is_heading and not marked_chapter:
                    ch_title = content[:90]
                size = 18 if tag == "h1" else (15 if tag == "h2" else (13 if tag in ("h3", "h4") else 11))
                fn = fonts.font_for(content, family="serif" if not is_heading else "sans", bold=is_heading)
                style = ParagraphStyle(
                    f"epub_{tag}_{idx}",
                    fontName=fn,
                    fontSize=size,
                    leading=size * 1.42,
                    spaceBefore=12 if is_heading else 2,
                    spaceAfter=8 if is_heading else 6,
                    leftIndent=18 if tag in ("blockquote", "li") else 0,
                )
                prefix = "• " if tag == "li" else ""
                para = Paragraph(prefix + _xml_escape(content), style)
                if not marked_chapter:
                    para._epub_chapter = ch_title
                    chapter_bookmarks.append(ch_title)
                    marked_chapter = True
                story.append(para)

        if not story:
            raise core.PdfManagerError("EPUB contains no extractable text or images")

        doc.build(story)
        buf.seek(0)

    reader = PdfReader(buf)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    for title in chapter_bookmarks:
        pno = page_marks.get(title)
        if pno and 1 <= pno <= len(writer.pages):
            writer.add_outline_item(title=title, page_number=pno - 1)
    if rtl:
        writer._root_object[NameObject("/ViewerPreferences")] = DictionaryObject(
            {NameObject("/Direction"): NameObject("/R2L")}
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as fh:
        writer.write(fh)
    return {"pages": len(writer.pages), "chapters": len(chapter_bookmarks), "rtl": rtl, "title": book_title, "output": str(dst)}
