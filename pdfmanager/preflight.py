# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Print preflight inspection and safe print-readiness fixes.

Inspects a PDF for common commercial-print issues:
  - Low-resolution images (effective rendered DPI from CTM + pixel dimensions)
  - Unembedded fonts and Type 3 fonts
  - Colour spaces (RGB vs CMYK vs Gray vs Spot/Separation) and transparency
  - Page box geometry (/MediaBox, /TrimBox, /BleedBox), mixed sizes, and bleed
  - Hairline strokes (< 0.25 pt) and rich-black small text
"""

import math
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from pypdf import PageObject, PdfWriter, Transformation
from pypdf.generic import ArrayObject, ContentStream, DecodedStreamObject, FloatObject, NameObject

from pdfmanager import core

PT_PER_MM = 72.0 / 25.4


def _mul_ctm(m1, m2):
    """Multiply two 6-element PDF affine matrices [a, b, c, d, e, f]."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    ]


def _transform_pt(ctm, x, y):
    a, b, c, d, e, f = ctm
    return a * x + c * y + e, b * x + d * y + f


def _unit_box_rect(ctm):
    pts = [_transform_pt(ctm, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def _fnum(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _cs_name(cs_obj):
    if cs_obj is None:
        return None
    if hasattr(cs_obj, "get_object"):
        cs_obj = cs_obj.get_object()
    if isinstance(cs_obj, NameObject):
        return str(cs_obj).lstrip("/")
    if isinstance(cs_obj, ArrayObject) and cs_obj:
        kind = str(cs_obj[0]).lstrip("/")
        if kind in ("Separation", "DeviceN") and len(cs_obj) > 1:
            return f"{kind}:{str(cs_obj[1]).lstrip('/')}"
        if kind == "ICCBased" and len(cs_obj) > 1:
            stream = cs_obj[1].get_object() if hasattr(cs_obj[1], "get_object") else cs_obj[1]
            n = int(stream.get("/N", 0)) if hasattr(stream, "get") else 0
            return {1: "ICCBased(Gray)", 3: "ICCBased(RGB)", 4: "ICCBased(CMYK)"}.get(n, "ICCBased")
        return kind
    return str(cs_obj).lstrip("/")


def _font_embedded(font_obj):
    """Return (embedded: bool, subtype: str, name: str)."""
    if hasattr(font_obj, "get_object"):
        font_obj = font_obj.get_object()
    subtype = str(font_obj.get("/Subtype", "")).lstrip("/")
    base_font = str(font_obj.get("/BaseFont", "")).lstrip("/") or str(font_obj.get("/Name", "")).lstrip("/") or "Unknown"
    if subtype == "Type3":
        return True, subtype, base_font
    desc = font_obj.get("/FontDescriptor")
    if desc is None and subtype == "Type0":
        descendants = font_obj.get("/DescendantFonts")
        if hasattr(descendants, "get_object"):
            descendants = descendants.get_object()
        if descendants:
            d0 = descendants[0].get_object() if hasattr(descendants[0], "get_object") else descendants[0]
            desc = d0.get("/FontDescriptor")
    if hasattr(desc, "get_object"):
        desc = desc.get_object()
    if desc is not None and hasattr(desc, "get"):
        if any(desc.get(k) is not None for k in ("/FontFile", "/FontFile2", "/FontFile3")):
            return True, subtype, base_font
    return False, subtype, base_font


def _has_extgstate_transparency(resources):
    gs_map = resources.get("/ExtGState") if resources else None
    if hasattr(gs_map, "get_object"):
        gs_map = gs_map.get_object()
    if not gs_map or not hasattr(gs_map, "items"):
        return False
    for _, ref in gs_map.items():
        gs = ref.get_object() if hasattr(ref, "get_object") else ref
        if not hasattr(gs, "get"):
            continue
        if _fnum(gs.get("/CA", 1.0), 1.0) < 0.999 or _fnum(gs.get("/ca", 1.0), 1.0) < 0.999:
            return True
        bm = gs.get("/BM")
        if bm is not None and str(bm).lstrip("/") not in ("Normal", "Compatible"):
            return True
        smask = gs.get("/SMask")
        if smask is not None and str(smask).lstrip("/") != "None":
            return True
    return False


def _box_tuple(box):
    if box is None:
        return None
    return (float(box.left), float(box.bottom), float(box.right), float(box.top))


def check(path, password=None, *, min_dpi=300, error_dpi=150, min_bleed_mm=3.0, min_stroke_pt=0.25):
    """Run print preflight checks on ``path`` and return a structured report."""
    reader = core.open_pdf(Path(path), password=password)
    issues = []
    color_spaces = set()
    page_sizes = []
    seen_unembedded = set()
    seen_type3 = set()
    min_bleed_pt = float(min_bleed_mm) * PT_PER_MM

    for page_idx, page in enumerate(reader.pages, start=1):
        mb = _box_tuple(page.mediabox)
        tb = _box_tuple(page.trimbox) if "/TrimBox" in page else None
        bb = _box_tuple(page.bleedbox) if "/BleedBox" in page else None
        w_pt = round(mb[2] - mb[0], 2)
        h_pt = round(mb[3] - mb[1], 2)
        bleed_mm = None
        if tb is not None:
            outer = bb or mb
            margins = (tb[0] - outer[0], tb[1] - outer[1], outer[2] - tb[2], outer[3] - tb[3])
            bleed_pt = min(margins)
            bleed_mm = round(max(0.0, bleed_pt) / PT_PER_MM, 2)
            if bleed_pt + 0.25 < min_bleed_pt:
                issues.append({
                    "severity": "warning",
                    "code": "missing_bleed",
                    "page": page_idx,
                    "message": f"Page {page_idx}: bleed is {bleed_mm:.1f} mm (below {min_bleed_mm:.1f} mm)",
                    "detail": "Extend artwork and /BleedBox beyond /TrimBox before commercial printing.",
                    "rect": list(mb),
                })
        page_sizes.append({
            "page": page_idx,
            "width_pt": w_pt,
            "height_pt": h_pt,
            "has_trim": tb is not None,
            "has_bleed": bb is not None,
            "bleed_mm": bleed_mm,
        })

        resources = page.get("/Resources")
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        resources = resources or {}

        # Transparency group or ExtGState alpha/blend
        group = page.get("/Group")
        if hasattr(group, "get_object"):
            group = group.get_object()
        has_trans = (group is not None and hasattr(group, "get") and str(group.get("/S", "")).lstrip("/") == "Transparency")
        if not has_trans:
            has_trans = _has_extgstate_transparency(resources)
        if has_trans:
            issues.append({
                "severity": "warning",
                "code": "transparency",
                "page": page_idx,
                "message": f"Page {page_idx}: uses transparency",
                "detail": "Flatten transparency if targeting older RIPs or PDF/X-1a.",
                "rect": None,
            })

        # Named colour spaces in Resources
        cs_map = resources.get("/ColorSpace")
        if hasattr(cs_map, "get_object"):
            cs_map = cs_map.get_object()
        if cs_map and hasattr(cs_map, "items"):
            for _, csref in cs_map.items():
                name = _cs_name(csref)
                if name:
                    color_spaces.add(name)

        # XObjects map for image DPI & image colour spaces
        xobjs = resources.get("/XObject")
        if hasattr(xobjs, "get_object"):
            xobjs = xobjs.get_object()
        xobjs = xobjs if (xobjs and hasattr(xobjs, "get")) else {}

        # Walk page content stream for CTM, images, color operators, hairlines, rich-black text, used fonts
        ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        stack = []
        line_width = 1.0
        fill_cmyk = None
        font_size = 12.0
        page_rgb = False
        hairline_flagged = False
        rich_black_flagged = False
        used_fonts = set()
        current_font = None

        try:
            cs = ContentStream(page.get_contents(), reader)
            ops = cs.operations
        except Exception:
            ops = []

        for operands, operator in ops:
            op = operator.decode("latin1") if isinstance(operator, bytes) else str(operator)
            if op == "q":
                stack.append((list(ctm), line_width, fill_cmyk, font_size, current_font))
            elif op == "Q" and stack:
                ctm, line_width, fill_cmyk, font_size, current_font = stack.pop()
            elif op == "cm" and len(operands) == 6:
                m = [_fnum(v) for v in operands]
                ctm = _mul_ctm(m, ctm)
            elif op == "w" and operands:
                line_width = _fnum(operands[0], 1.0)
            elif op in ("rg", "RG"):
                color_spaces.add("DeviceRGB")
                page_rgb = True
                if op == "rg":
                    fill_cmyk = None
            elif op in ("k", "K"):
                color_spaces.add("DeviceCMYK")
                if op == "k" and len(operands) == 4:
                    fill_cmyk = tuple(_fnum(v) for v in operands)
            elif op in ("g", "G"):
                color_spaces.add("DeviceGray")
                if op == "g":
                    fill_cmyk = None
            elif op == "Tf" and len(operands) >= 2:
                current_font = str(operands[0])
                font_size = abs(_fnum(operands[1], 12.0))
            elif op in ("Tj", "TJ", "'", '"'):
                if current_font is not None:
                    used_fonts.add(current_font)
                if (
                    not rich_black_flagged
                    and fill_cmyk is not None
                    and font_size <= 12.0
                    and sum(fill_cmyk) >= 2.4
                    and fill_cmyk[3] >= 0.5
                    and any(c >= 0.2 for c in fill_cmyk[:3])
                ):
                    rich_black_flagged = True
                    issues.append({
                        "severity": "warning",
                        "code": "rich_black_text",
                        "page": page_idx,
                        "message": f"Page {page_idx}: small text ({font_size:.1f} pt) uses rich black CMYK",
                        "detail": "Use pure 100% K (0/0/0/1) for small body text to avoid misregistration.",
                        "rect": None,
                    })
            elif op in ("S", "s", "B", "B*", "b", "b*"):
                scale = math.sqrt(max(1e-9, abs(ctm[0] * ctm[3] - ctm[1] * ctm[2])))
                eff_w = line_width * scale
                if not hairline_flagged and 0.0 < eff_w < float(min_stroke_pt):
                    hairline_flagged = True
                    issues.append({
                        "severity": "warning",
                        "code": "hairline_stroke",
                        "page": page_idx,
                        "message": f"Page {page_idx}: hairline stroke ({eff_w:.2f} pt < {min_stroke_pt:.2f} pt)",
                        "detail": "Very thin strokes may disappear on commercial presses.",
                        "rect": None,
                    })
            elif op == "Do" and operands:
                xname = operands[0]
                xobj = xobjs.get(xname)
                if hasattr(xobj, "get_object"):
                    xobj = xobj.get_object()
                if not xobj or not hasattr(xobj, "get"):
                    continue
                if str(xobj.get("/Subtype", "")).lstrip("/") == "Image":
                    img_cs = _cs_name(xobj.get("/ColorSpace"))
                    if img_cs:
                        color_spaces.add(img_cs)
                        if "RGB" in img_cs:
                            page_rgb = True
                    px_w = int(_fnum(xobj.get("/Width", 0)))
                    px_h = int(_fnum(xobj.get("/Height", 0)))
                    draw_w_in = math.hypot(ctm[0], ctm[1]) / 72.0
                    draw_h_in = math.hypot(ctm[2], ctm[3]) / 72.0
                    if px_w > 0 and px_h > 0 and draw_w_in > 1e-4 and draw_h_in > 1e-4:
                        dpi_x = px_w / draw_w_in
                        dpi_y = px_h / draw_h_in
                        eff_dpi = min(dpi_x, dpi_y)
                        if eff_dpi < float(min_dpi):
                            sev = "error" if eff_dpi < float(error_dpi) else "warning"
                            issues.append({
                                "severity": sev,
                                "code": "low_dpi",
                                "page": page_idx,
                                "message": f"Page {page_idx}: image at {round(eff_dpi)} DPI ({px_w}×{px_h} px)",
                                "detail": f"Effective print resolution is below {min_dpi} DPI.",
                                "rect": _unit_box_rect(ctm),
                            })

        # Fonts actually used on this page (skips unused placeholder fonts in /Resources)
        fonts_map = resources.get("/Font")
        if hasattr(fonts_map, "get_object"):
            fonts_map = fonts_map.get_object()
        if fonts_map and hasattr(fonts_map, "items"):
            for fkey, fref in fonts_map.items():
                if ops and str(fkey) not in used_fonts:
                    continue
                embedded, subtype, fname = _font_embedded(fref)
                if subtype == "Type3" and (page_idx, fname) not in seen_type3:
                    seen_type3.add((page_idx, fname))
                    issues.append({
                        "severity": "warning",
                        "code": "type3_font",
                        "page": page_idx,
                        "message": f"Page {page_idx}: Type 3 font '{fname}'",
                        "detail": "Type 3 fonts may render poorly or rasterize at print time.",
                        "rect": None,
                    })
                elif not embedded and (page_idx, fname) not in seen_unembedded:
                    seen_unembedded.add((page_idx, fname))
                    issues.append({
                        "severity": "error",
                        "code": "unembedded_font",
                        "page": page_idx,
                        "message": f"Page {page_idx}: font '{fname}' is not embedded",
                        "detail": "Unembedded fonts can substitute unpredictably on a print press.",
                        "rect": None,
                    })

        if page_rgb:
            issues.append({
                "severity": "warning",
                "code": "rgb_color",
                "page": page_idx,
                "message": f"Page {page_idx}: contains RGB colour",
                "detail": "Convert RGB content to CMYK or grayscale before offset printing.",
                "rect": None,
            })

    # Inconsistent page sizes across document
    if len(page_sizes) > 1:
        first_w, first_h = page_sizes[0]["width_pt"], page_sizes[0]["height_pt"]
        for ps in page_sizes[1:]:
            if abs(ps["width_pt"] - first_w) > 1.0 or abs(ps["height_pt"] - first_h) > 1.0:
                issues.append({
                    "severity": "warning",
                    "code": "mixed_page_size",
                    "page": ps["page"],
                    "message": f"Page {ps['page']}: page size ({ps['width_pt']:.0f}×{ps['height_pt']:.0f} pt) differs from page 1 ({first_w:.0f}×{first_h:.0f} pt)",
                    "detail": "Normalize page sizes if printing as a single bound job.",
                    "rect": None,
                })

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")
    info = sum(1 for i in issues if i["severity"] == "info")
    return {
        "passed": errors == 0,
        "pages": len(page_sizes),
        "summary": {"errors": errors, "warnings": warnings, "info": info},
        "color_spaces": sorted(color_spaces),
        "page_sizes": page_sizes,
        "issues": issues,
    }


def _crop_marks_stream(trim_box, bleed_pt):
    """Build PDF drawing operators for corner crop marks outside ``trim_box``."""
    x0, y0, x1, y1 = trim_box
    gap = max(3.0, min(6.0, bleed_pt * 0.4))
    length = max(6.0, bleed_pt - gap)
    lines = ["q 0.5 w 0 0 0 1 K"]
    corners = [
        # (hx0, hy, hx1, hy), (vx, vy0, vx, vy1)
        ((x0 - gap - length, y0, x0 - gap, y0), (x0, y0 - gap - length, x0, y0 - gap)),
        ((x1 + gap, y0, x1 + gap + length, y0), (x1, y0 - gap - length, x1, y0 - gap)),
        ((x0 - gap - length, y1, x0 - gap, y1), (x0, y1 + gap, x0, y1 + gap + length)),
        ((x1 + gap, y1, x1 + gap + length, y1), (x1, y1 + gap, x1, y1 + gap + length)),
    ]
    for (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) in corners:
        lines.append(f"{ax0:.2f} {ay0:.2f} m {ax1:.2f} {ay1:.2f} l S")
        lines.append(f"{bx0:.2f} {by0:.2f} m {bx1:.2f} {by1:.2f} l S")
    lines.append("Q\n")
    return "\n".join(lines).encode("ascii")


def _ghostscript_bin():
    for name in ("gs", "gswin64c", "gswin32c"):
        found = shutil.which(name)
        if found:
            return found
    return None


def fix(
    input_path,
    output_path,
    password=None,
    *,
    normalize_size=False,
    add_bleed_mm=0.0,
    add_crop_marks=False,
    color_strategy=None,
    flatten_transparency=False,
):
    """Apply safe print-readiness fixes and write the resulting PDF to ``output_path``."""
    src = Path(input_path)
    dst = Path(output_path)
    reader = core.open_pdf(src, password=password)
    writer = PdfWriter()

    bleed_mm = float(add_bleed_mm or 0.0)
    if add_crop_marks and bleed_mm <= 0.0:
        bleed_mm = 3.0
    bleed_pt = bleed_mm * PT_PER_MM

    target_size = None
    if normalize_size and reader.pages:
        dims = [(round(float(p.mediabox.width), 1), round(float(p.mediabox.height), 1)) for p in reader.pages]
        target_size = Counter(dims).most_common(1)[0][0]

    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        if target_size and (abs(w - target_size[0]) > 1.0 or abs(h - target_size[1]) > 1.0):
            tw, th = target_size
            blank = PageObject.create_blank_page(width=tw, height=th)
            dx = (tw - w) / 2.0
            dy = (th - h) / 2.0
            blank.merge_transformed_page(page, Transformation().translate(dx, dy))
            page = blank
            w, h = tw, th

        out_page = writer.add_page(page)
        if bleed_pt > 0.0:
            x0, y0, x1, y1 = _box_tuple(out_page.mediabox)
            trim = (x0, y0, x1, y1)
            new_media = (x0 - bleed_pt, y0 - bleed_pt, x1 + bleed_pt, y1 + bleed_pt)
            out_page[NameObject("/MediaBox")] = ArrayObject([FloatObject(v) for v in new_media])
            out_page[NameObject("/CropBox")] = ArrayObject([FloatObject(v) for v in new_media])
            out_page[NameObject("/BleedBox")] = ArrayObject([FloatObject(v) for v in new_media])
            out_page[NameObject("/TrimBox")] = ArrayObject([FloatObject(v) for v in trim])
            if add_crop_marks:
                mark_page = PageObject.create_blank_page(width=new_media[2] - new_media[0], height=new_media[3] - new_media[1])
                mark_page[NameObject("/MediaBox")] = ArrayObject([FloatObject(v) for v in new_media])
                stream = DecodedStreamObject()
                stream.set_data(_crop_marks_stream(trim, bleed_pt))
                mark_page[NameObject("/Contents")] = stream
                out_page.merge_page(mark_page)

    if color_strategy or flatten_transparency:
        gs = _ghostscript_bin()
        if not gs:
            raise core.PdfManagerError("Ghostscript is required for CMYK/grayscale conversion or transparency flattening")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mid = Path(tmp) / "mid.pdf"
            with open(mid, "wb") as fh:
                writer.write(fh)
            cmd = [gs, "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pdfwrite", f"-sOutputFile={dst}"]
            if flatten_transparency:
                cmd.append("-dCompatibilityLevel=1.3")
            if color_strategy == "cmyk":
                cmd += ["-sColorConversionStrategy=CMYK", "-dProcessColorModel=/DeviceCMYK"]
            elif color_strategy == "gray":
                cmd += ["-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray"]
            cmd.append(str(mid))
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise core.PdfManagerError(f"Ghostscript failed: {(proc.stderr or proc.stdout).strip()[:200]}")
    else:
        with open(dst, "wb") as fh:
            writer.write(fh)

    return dst


def report(result, name=""):
    """Format a human-readable print preflight report."""
    header = f"Print preflight: {name}" if name else "Print preflight"
    s = result["summary"]
    status = "PASSED" if result["passed"] else "FAILED"
    lines = [
        f"{header} — {status} ({result['pages']} pages; {s['errors']} errors, {s['warnings']} warnings)",
    ]
    if result["color_spaces"]:
        lines.append("  Colour spaces: " + ", ".join(result["color_spaces"]))
    if not result["issues"]:
        lines.append("  No print issues found.")
    else:
        for issue in result["issues"]:
            tag = issue["severity"].upper()
            lines.append(f"  [{tag}] {issue['message']}")
    return "\n".join(lines) + "\n"
