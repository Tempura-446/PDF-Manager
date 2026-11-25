# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Print-production CMYK preflight, PDF/X-1a & PDF/X-4 compliance, plate separation, and safe fixes.

Inspects a PDF for commercial-print issues:
  - Low-resolution images (effective rendered DPI from CTM + pixel dimensions)
  - Total Ink / Area Coverage (TAC %) across CMYK vectors and CMYK Image XObjects
  - Overprint & knockout hazards (white overprint disappearing on press, small black text knockout)
  - PDF/X-1a and PDF/X-4 structural & colour rules (/OutputIntents, /Trapped, /TrimBox, encryption, transparency)
  - Unembedded fonts and Type 3 fonts
  - Colour spaces (RGB vs CMYK vs Gray vs Spot/Separation) and transparency
  - Page box geometry (/MediaBox, /TrimBox, /BleedBox), mixed sizes, and bleed
  - Hairline strokes (< 0.25 pt) and rich-black small text
  - True C / M / Y / K plate separation & TAC heatmap preview rendering
"""

import io
import logging
import math
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import (
    ArrayObject, BooleanObject, ContentStream, DecodedStreamObject,
    DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject,
)

from pdfmanager import core

PT_PER_MM = 72.0 / 25.4
PROFILES = ("standard", "pdfx-1a", "pdfx-4")
PLATES = ("all", "c", "m", "y", "k", "tac")


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


def _pbool(val):
    """Extract a true Python bool from a pypdf BooleanObject, NameObject, or bool."""
    if val is None:
        return None
    if hasattr(val, "get_object"):
        val = val.get_object()
    if isinstance(val, bool):
        return val
    if hasattr(val, "value"):
        return bool(val.value)
    return str(val).strip().lstrip("/").lower() == "true"


def _parse_extgstate_overprint(resources):
    """Return ``{gs_name: (op_stroke_or_none, op_fill_or_none, opm_or_none)}``."""
    out = {}
    gs_map = resources.get("/ExtGState") if resources else None
    if hasattr(gs_map, "get_object"):
        gs_map = gs_map.get_object()
    if not gs_map or not hasattr(gs_map, "items"):
        return out
    for key, ref in gs_map.items():
        gs = ref.get_object() if hasattr(ref, "get_object") else ref
        if not hasattr(gs, "get"):
            continue
        b_s = _pbool(gs.get("/OP"))
        op_f = _pbool(gs.get("/op"))
        # Per PDF spec, if /op is absent but /OP is present, /OP also governs fill overprint
        b_f = op_f if op_f is not None else b_s
        opm = gs.get("/OPM")
        i_opm = int(_fnum(opm, 0)) if opm is not None else None
        out[str(key)] = (b_s, b_f, i_opm)
    return out


def _box_tuple(box):
    if box is None:
        return None
    return (float(box.left), float(box.bottom), float(box.right), float(box.top))


def _image_cmyk_tac_pct(xobj, px_w, px_h):
    """Estimate peak Total Ink Coverage (%) for a CMYK Image XObject."""
    import numpy as np

    if px_w <= 0 or px_h <= 0:
        return 0.0
    arr = None
    try:
        data = xobj.get_data()
        if len(data) == px_w * px_h * 4:
            arr = np.frombuffer(data, dtype=np.uint8).reshape((px_h, px_w, 4))
    except Exception as _exc:
        logging.getLogger("zpdf").debug("preflight._image_cmyk_tac_pct.raw: %s", _exc)

    if arr is None:
        try:
            from PIL import Image

            raw_bytes = getattr(xobj, "_data", None) or xobj.get_data()
            with Image.open(io.BytesIO(raw_bytes)) as im:
                if im.mode == "CMYK":
                    arr = np.asarray(im, dtype=np.uint8)
        except Exception as _exc:
            logging.getLogger("zpdf").debug("preflight._image_cmyk_tac_pct.pil: %s", _exc)

    if arr is None or arr.size == 0:
        return 0.0

    # Respect explicit /Decode [1 0 1 0 1 0 1 0] inversion when present
    decode = xobj.get("/Decode")
    if hasattr(decode, "get_object"):
        decode = decode.get_object()
    if isinstance(decode, (list, ArrayObject)) and len(decode) >= 8:
        if _fnum(decode[0], 0.0) == 1.0 and _fnum(decode[1], 1.0) == 0.0:
            arr = 255 - arr

    tac = arr.astype(np.float32).sum(axis=2) * (100.0 / 255.0)
    if px_w * px_h > 64:
        return float(np.percentile(tac, 99.5))
    return float(tac.max())


def _check_pdfx_document(reader, profile):
    """Check document-level ISO 15930 (PDF/X-1a & PDF/X-4) requirements."""
    issues = []
    if reader.is_encrypted:
        issues.append({
            "severity": "error",
            "code": "pdfx_encrypted",
            "page": None,
            "message": f"{profile.upper()}: encrypted PDFs are prohibited",
            "detail": "Remove password protection before exporting for PDF/X print workflows.",
            "rect": None,
        })

    root = reader.trailer.get("/Root")
    if hasattr(root, "get_object"):
        root = root.get_object()
    root = root or {}

    # 1. /OutputIntents with /S /GTS_PDFX
    intents = root.get("/OutputIntents")
    if hasattr(intents, "get_object"):
        intents = intents.get_object()
    has_pdfx_intent = False
    if isinstance(intents, (list, ArrayObject)):
        for item in intents:
            obj = item.get_object() if hasattr(item, "get_object") else item
            if not hasattr(obj, "get"):
                continue
            sub = str(obj.get("/S", "")).lstrip("/")
            cond = str(obj.get("/OutputConditionIdentifier", "")).strip()
            dest = obj.get("/DestOutputProfile")
            if sub == "GTS_PDFX" and (cond or dest is not None):
                has_pdfx_intent = True
                break
    if not has_pdfx_intent:
        issues.append({
            "severity": "error",
            "code": "missing_output_intent",
            "page": None,
            "message": f"{profile.upper()}: missing GTS_PDFX /OutputIntents profile",
            "detail": "Attach a characterization intent (e.g. CGATS TR 001 / FOGRA39) so the press knows the target CMYK condition.",
            "rect": None,
        })

    # 2. /Info /Trapped must be /True or /False
    info_dict = reader.trailer.get("/Info")
    if hasattr(info_dict, "get_object"):
        info_dict = info_dict.get_object()
    trapped_val = ""
    if info_dict and hasattr(info_dict, "get"):
        raw_t = info_dict.get("/Trapped")
        trapped_val = str(raw_t or "").lstrip("/").strip()
    if trapped_val not in ("True", "False"):
        issues.append({
            "severity": "error",
            "code": "missing_trapped_key",
            "page": None,
            "message": f"{profile.upper()}: /Info /Trapped key must be /True or /False",
            "detail": "PDF/X requires explicit trapping status (/Trapped /False for untrapped files).",
            "rect": None,
        })

    # 3. Prohibited JavaScript actions
    names = root.get("/Names")
    if hasattr(names, "get_object"):
        names = names.get_object()
    open_act = root.get("/OpenAction")
    if hasattr(open_act, "get_object"):
        open_act = open_act.get_object()
    has_js = bool(names and hasattr(names, "get") and names.get("/JavaScript") is not None)
    if not has_js and open_act and hasattr(open_act, "get"):
        has_js = str(open_act.get("/S", "")).lstrip("/") == "JavaScript"
    if has_js:
        issues.append({
            "severity": "error",
            "code": "pdfx_javascript",
            "page": None,
            "message": f"{profile.upper()}: JavaScript actions are prohibited",
            "detail": "Sanitize interactive scripts before press submission.",
            "rect": None,
        })

    return issues


def check(
    path,
    password=None,
    *,
    min_dpi=300,
    error_dpi=150,
    min_bleed_mm=3.0,
    min_stroke_pt=0.25,
    max_tac=300.0,
    profile="standard",
):
    """Run print-production preflight checks on ``path`` and return a structured report."""
    norm_profile = str(profile or "standard").strip().lower()
    if norm_profile not in PROFILES:
        raise core.PdfManagerError(f"unknown preflight profile: {profile!r} (expected one of {PROFILES})")
    is_pdfx = norm_profile in ("pdfx-1a", "pdfx-4")
    tac_limit = float(max_tac)

    reader = core.open_pdf(Path(path), password=password)
    issues = []
    if is_pdfx:
        issues.extend(_check_pdfx_document(reader, norm_profile))

    color_spaces = set()
    page_sizes = []
    seen_unembedded = set()
    seen_type3 = set()
    min_bleed_pt = float(min_bleed_mm) * PT_PER_MM
    doc_max_tac = 0.0

    for page_idx, page in enumerate(reader.pages, start=1):
        mb = _box_tuple(page.mediabox)
        tb = _box_tuple(page.trimbox) if "/TrimBox" in page else None
        ab = _box_tuple(page.artbox) if "/ArtBox" in page else None
        bb = _box_tuple(page.bleedbox) if "/BleedBox" in page else None
        w_pt = round(mb[2] - mb[0], 2)
        h_pt = round(mb[3] - mb[1], 2)
        bleed_mm = None

        if is_pdfx:
            if tb is None and ab is None:
                issues.append({
                    "severity": "error",
                    "code": "missing_trim_or_art_box",
                    "page": page_idx,
                    "message": f"Page {page_idx}: {norm_profile.upper()} requires /TrimBox or /ArtBox",
                    "detail": "Define /TrimBox to specify finished trim dimensions.",
                    "rect": list(mb),
                })
            elif tb is not None and ab is not None:
                issues.append({
                    "severity": "error",
                    "code": "both_trim_and_art_box",
                    "page": page_idx,
                    "message": f"Page {page_idx}: {norm_profile.upper()} prohibits having both /TrimBox and /ArtBox",
                    "detail": "Keep /TrimBox and remove /ArtBox for PDF/X compliance.",
                    "rect": list(mb),
                })

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
            trans_sev = "error" if norm_profile == "pdfx-1a" else ("info" if norm_profile == "pdfx-4" else "warning")
            issues.append({
                "severity": trans_sev,
                "code": "transparency",
                "page": page_idx,
                "message": f"Page {page_idx}: uses live transparency",
                "detail": "Flatten transparency if targeting older RIPs or PDF/X-1a (allowed in PDF/X-4).",
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

        gs_op_map = _parse_extgstate_overprint(resources)

        # XObjects map for image DPI, colour spaces & CMYK TAC
        xobjs = resources.get("/XObject")
        if hasattr(xobjs, "get_object"):
            xobjs = xobjs.get_object()
        xobjs = xobjs if (xobjs and hasattr(xobjs, "get")) else {}

        # Graphics state tracking
        ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        stack = []
        line_width = 1.0
        fill_cmyk = None
        stroke_cmyk = None
        fill_white = False
        stroke_white = False
        op_fill = False
        op_stroke = False
        op_fill_explicit = False
        font_size = 12.0
        current_font = None

        page_rgb = False
        hairline_flagged = False
        rich_black_flagged = False
        white_op_flagged = False
        black_ko_flagged = False
        vec_tac_flagged = False
        page_max_tac = 0.0
        used_fonts = set()

        try:
            cs = ContentStream(page.get_contents(), reader)
            ops = cs.operations
        except Exception as _exc:
            logging.getLogger("zpdf").debug("preflight.check.stream: %s", _exc)
            ops = []

        def _record_vec_tac(cmyk_tuple):
            nonlocal page_max_tac, vec_tac_flagged
            if cmyk_tuple is None:
                return
            tac = sum(max(0.0, min(1.0, v)) for v in cmyk_tuple) * 100.0
            if tac > page_max_tac:
                page_max_tac = tac
            if not vec_tac_flagged and tac > tac_limit:
                vec_tac_flagged = True
                sev = "error" if tac > tac_limit + 40.0 else "warning"
                issues.append({
                    "severity": sev,
                    "code": "high_tac",
                    "page": page_idx,
                    "message": f"Page {page_idx}: vector CMYK total ink coverage is {tac:.0f}% (exceeds {tac_limit:.0f}%)",
                    "detail": "Reduce rich-CMYK channels or convert with a GCR ICC profile to prevent ink set-off on press.",
                    "rect": None,
                })

        def _check_white_overprint(is_fill_draw, is_stroke_draw):
            nonlocal white_op_flagged
            if white_op_flagged:
                return
            if (is_fill_draw and op_fill and fill_white) or (is_stroke_draw and op_stroke and stroke_white):
                white_op_flagged = True
                issues.append({
                    "severity": "error",
                    "code": "white_overprint",
                    "page": page_idx,
                    "message": f"Page {page_idx}: white object or text is set to overprint (will disappear on press)",
                    "detail": "White ink (0/0/0/0 CMYK) with overprint adds zero ink on press instead of knocking out background colours.",
                    "rect": None,
                })

        for operands, operator in ops:
            op = operator.decode("latin1") if isinstance(operator, bytes) else str(operator)
            if op == "q":
                stack.append((
                    list(ctm), line_width, fill_cmyk, stroke_cmyk,
                    fill_white, stroke_white, op_fill, op_stroke, op_fill_explicit,
                    font_size, current_font,
                ))
            elif op == "Q" and stack:
                (
                    ctm, line_width, fill_cmyk, stroke_cmyk,
                    fill_white, stroke_white, op_fill, op_stroke, op_fill_explicit,
                    font_size, current_font,
                ) = stack.pop()
            elif op == "cm" and len(operands) == 6:
                m = [_fnum(v) for v in operands]
                ctm = _mul_ctm(m, ctm)
            elif op == "w" and operands:
                line_width = _fnum(operands[0], 1.0)
            elif op == "gs" and operands:
                gname = str(operands[0])
                if gname in gs_op_map:
                    b_s, b_f, _ = gs_op_map[gname]
                    if b_s is not None:
                        op_stroke = b_s
                    if b_f is not None:
                        op_fill = b_f
                        op_fill_explicit = True
            elif op in ("rg", "RG") and len(operands) >= 3:
                color_spaces.add("DeviceRGB")
                page_rgb = True
                vals = tuple(_fnum(v) for v in operands[:3])
                is_w = all(v >= 0.995 for v in vals)
                if op == "rg":
                    fill_cmyk = None
                    fill_white = is_w
                else:
                    stroke_cmyk = None
                    stroke_white = is_w
            elif op in ("k", "K") and len(operands) >= 4:
                color_spaces.add("DeviceCMYK")
                vals = tuple(_fnum(v) for v in operands[:4])
                is_w = all(v <= 0.005 for v in vals)
                if op == "k":
                    fill_cmyk = vals
                    fill_white = is_w
                else:
                    stroke_cmyk = vals
                    stroke_white = is_w
            elif op in ("g", "G") and operands:
                color_spaces.add("DeviceGray")
                gv = _fnum(operands[0], 0.0)
                is_w = gv >= 0.995
                if op == "g":
                    fill_cmyk = (0.0, 0.0, 0.0, max(0.0, min(1.0, 1.0 - gv)))
                    fill_white = is_w
                else:
                    stroke_cmyk = (0.0, 0.0, 0.0, max(0.0, min(1.0, 1.0 - gv)))
                    stroke_white = is_w
            elif op == "Tf" and len(operands) >= 2:
                current_font = str(operands[0])
                font_size = abs(_fnum(operands[1], 12.0))
            elif op in ("Tj", "TJ", "'", '"'):
                if current_font is not None:
                    used_fonts.add(current_font)
                _record_vec_tac(fill_cmyk)
                _check_white_overprint(True, False)
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
                if (
                    not black_ko_flagged
                    and fill_cmyk is not None
                    and font_size <= 12.0
                    and fill_cmyk[3] >= 0.95
                    and all(c <= 0.01 for c in fill_cmyk[:3])
                    and op_fill_explicit
                    and not op_fill
                ):
                    black_ko_flagged = True
                    issues.append({
                        "severity": "warning",
                        "code": "black_knockout_text",
                        "page": page_idx,
                        "message": f"Page {page_idx}: small pure-black text ({font_size:.1f} pt) is set to knockout (/op false)",
                        "detail": "Overprinting 100% K body text avoids white misregistration halos over coloured backgrounds.",
                        "rect": None,
                    })
            elif op in ("f", "F", "f*"):
                _record_vec_tac(fill_cmyk)
                _check_white_overprint(True, False)
            elif op in ("S", "s", "B", "B*", "b", "b*"):
                is_both = op in ("B", "B*", "b", "b*")
                if is_both:
                    _record_vec_tac(fill_cmyk)
                _record_vec_tac(stroke_cmyk)
                _check_white_overprint(is_both, True)
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
                    img_rect = _unit_box_rect(ctm)
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
                                "rect": img_rect,
                            })
                    if img_cs and "CMYK" in img_cs:
                        img_tac = _image_cmyk_tac_pct(xobj, px_w, px_h)
                        if img_tac > page_max_tac:
                            page_max_tac = img_tac
                        if img_tac > tac_limit:
                            sev = "error" if img_tac > tac_limit + 40.0 else "warning"
                            issues.append({
                                "severity": sev,
                                "code": "high_tac",
                                "page": page_idx,
                                "message": f"Page {page_idx}: CMYK image total ink coverage reaches {img_tac:.0f}% (exceeds {tac_limit:.0f}%)",
                                "detail": "Convert image with an ICC profile matching your paper stock's ink limit (e.g. 300% coated, 280% uncoated).",
                                "rect": img_rect,
                            })

        if page_max_tac > doc_max_tac:
            doc_max_tac = page_max_tac

        page_sizes.append({
            "page": page_idx,
            "width_pt": w_pt,
            "height_pt": h_pt,
            "has_trim": tb is not None,
            "has_bleed": bb is not None,
            "bleed_mm": bleed_mm,
            "max_tac_pct": round(page_max_tac, 1),
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
            rgb_sev = "error" if is_pdfx else "warning"
            issues.append({
                "severity": rgb_sev,
                "code": "rgb_color",
                "page": page_idx,
                "message": f"Page {page_idx}: contains RGB colour",
                "detail": "Convert RGB content to CMYK or grayscale before offset/PDF-X printing.",
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
        "profile": norm_profile,
        "pages": len(page_sizes),
        "max_tac_pct": round(doc_max_tac, 1),
        "max_tac_limit": tac_limit,
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


def _neutralize_white_overprint_on_page(out_page, writer):
    """Insert an ``/OP false /op false`` graphics state override whenever white
    colour is selected while overprint is active, leaving non-white overprints intact."""
    resources = out_page.get("/Resources")
    if hasattr(resources, "get_object"):
        resources = resources.get_object()
    if not resources or not hasattr(resources, "get"):
        return
    gs_op_map = _parse_extgstate_overprint(resources)
    if not gs_op_map:
        return
    try:
        cs = ContentStream(out_page.get_contents(), out_page.pdf)
        ops = cs.operations
    except Exception as _exc:
        logging.getLogger("zpdf").debug("preflight._neutralize_white_overprint_on_page: %s", _exc)
        return
    if not ops:
        return

    noop_name = NameObject("/ZPDFNoOP")
    new_ops = []
    op_fill = False
    op_stroke = False
    stack = []
    changed = False

    for operands, operator in ops:
        op = operator.decode("latin1") if isinstance(operator, bytes) else str(operator)
        new_ops.append((operands, operator))
        if op == "q":
            stack.append((op_fill, op_stroke))
        elif op == "Q" and stack:
            op_fill, op_stroke = stack.pop()
        elif op == "gs" and operands:
            gname = str(operands[0])
            if gname in gs_op_map:
                b_s, b_f, _ = gs_op_map[gname]
                if b_s is not None:
                    op_stroke = b_s
                if b_f is not None:
                    op_fill = b_f
        elif op in ("k", "K") and len(operands) >= 4:
            vals = tuple(_fnum(v) for v in operands[:4])
            if all(v <= 0.005 for v in vals):
                if (op == "k" and op_fill) or (op == "K" and op_stroke):
                    new_ops.append(([noop_name], b"gs"))
                    op_fill = False
                    op_stroke = False
                    changed = True
        elif op in ("rg", "RG") and len(operands) >= 3:
            vals = tuple(_fnum(v) for v in operands[:3])
            if all(v >= 0.995 for v in vals):
                if (op == "rg" and op_fill) or (op == "RG" and op_stroke):
                    new_ops.append(([noop_name], b"gs"))
                    op_fill = False
                    op_stroke = False
                    changed = True
        elif op in ("g", "G") and operands:
            if _fnum(operands[0], 0.0) >= 0.995:
                if (op == "g" and op_fill) or (op == "G" and op_stroke):
                    new_ops.append(([noop_name], b"gs"))
                    op_fill = False
                    op_stroke = False
                    changed = True

    if changed:
        gs_dict = resources.get("/ExtGState")
        if hasattr(gs_dict, "get_object"):
            gs_dict = gs_dict.get_object()
        if not isinstance(gs_dict, DictionaryObject):
            gs_dict = DictionaryObject()
            resources[NameObject("/ExtGState")] = gs_dict
        gs_dict[noop_name] = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/ExtGState"),
            NameObject("/OP"): BooleanObject(False),
            NameObject("/op"): BooleanObject(False),
        }))
        cs.operations = new_ops
        out_page[NameObject("/Contents")] = writer._add_object(cs)


def _attach_pdfx_output_intent(writer, condition_id="CGATS TR 001", condition_desc="SWOP CGATS TR 001 Coated"):
    """Attach a GTS_PDFX /OutputIntents entry, set /Info /Trapped /False, and ensure /TrimBox on all pages."""
    info = writer._info
    if hasattr(info, "get_object"):
        info = info.get_object()
    if isinstance(info, DictionaryObject):
        info[NameObject("/Trapped")] = NameObject("/False")

    for page in writer.pages:
        if "/TrimBox" not in page and "/ArtBox" not in page:
            mb = _box_tuple(page.mediabox)
            page[NameObject("/TrimBox")] = ArrayObject([FloatObject(v) for v in mb])
        elif "/TrimBox" in page and "/ArtBox" in page:
            del page[NameObject("/ArtBox")]

    intent_dict = DictionaryObject({
        NameObject("/Type"): NameObject("/OutputIntent"),
        NameObject("/S"): NameObject("/GTS_PDFX"),
        NameObject("/OutputConditionIdentifier"): TextStringObject(condition_id),
        NameObject("/OutputCondition"): TextStringObject(condition_desc),
        NameObject("/RegistryName"): TextStringObject("http://www.color.org"),
    })
    intent_ref = writer._add_object(intent_dict)
    writer._root_object[NameObject("/OutputIntents")] = ArrayObject([intent_ref])


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
    fix_white_overprint=False,
    add_pdfx_intent=False,
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
        if fix_white_overprint:
            _neutralize_white_overprint_on_page(out_page, writer)
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

    if add_pdfx_intent:
        _attach_pdfx_output_intent(writer)

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


# ---- True CMYK Plate Separation & TAC Heatmap Preview ---------------------------------


def _rgb_to_cmyk_tuple(r, g, b):
    r = max(0.0, min(1.0, r))
    g = max(0.0, min(1.0, g))
    b = max(0.0, min(1.0, b))
    k = 1.0 - max(r, g, b)
    if k >= 0.9999:
        return (0.0, 0.0, 0.0, 1.0)
    inv = 1.0 / (1.0 - k)
    return ((1.0 - r - k) * inv, (1.0 - g - k) * inv, (1.0 - b - k) * inv, k)


def _plate_rgb_values(c, m, y, k, plate_ch, *, tinted=True):
    val = max(0.0, min(1.0, {"c": c, "m": m, "y": y, "k": k}[plate_ch]))
    if not tinted or plate_ch == "k":
        g = 1.0 - val
        return (g, g, g)
    if plate_ch == "c":
        return (1.0 - val, 1.0 - 0.16 * val, 1.0)
    if plate_ch == "m":
        return (1.0, 1.0 - val, 1.0 - 0.14 * val)
    return (1.0, 1.0 - 0.04 * val, 1.0 - val)


def _separate_image_xobject_for_plate(xobj, plate_ch, *, tinted=True):
    """Rewrite an Image XObject in-place into a DeviceRGB image showing ``plate_ch``."""
    import numpy as np
    from PIL import Image

    px_w = int(_fnum(xobj.get("/Width", 0)))
    px_h = int(_fnum(xobj.get("/Height", 0)))
    if px_w <= 0 or px_h <= 0:
        return
    cs = _cs_name(xobj.get("/ColorSpace")) or ""
    ch_idx = {"c": 0, "m": 1, "y": 2, "k": 3}[plate_ch]

    plate_v = None  # float32 0..1 ink intensity
    try:
        data = xobj.get_data()
        if "CMYK" in cs and len(data) == px_w * px_h * 4:
            arr = np.frombuffer(data, dtype=np.uint8).reshape((px_h, px_w, 4))
            plate_v = arr[:, :, ch_idx].astype(np.float32) / 255.0
        elif "Gray" in cs and len(data) == px_w * px_h:
            arr = np.frombuffer(data, dtype=np.uint8).reshape((px_h, px_w))
            plate_v = (1.0 - arr.astype(np.float32) / 255.0) if plate_ch == "k" else np.zeros((px_h, px_w), dtype=np.float32)
        elif "RGB" in cs and len(data) == px_w * px_h * 3:
            rgb = np.frombuffer(data, dtype=np.uint8).reshape((px_h, px_w, 3)).astype(np.float32) / 255.0
            k = 1.0 - rgb.max(axis=2)
            if plate_ch == "k":
                plate_v = k
            else:
                rgb_i = {"c": 0, "m": 1, "y": 2}[plate_ch]
                denom = np.maximum(1e-5, 1.0 - k)
                plate_v = np.where(k >= 0.999, 0.0, (1.0 - rgb[:, :, rgb_i] - k) / denom)
    except Exception as _exc:
        logging.getLogger("zpdf").debug("preflight._separate_image_xobject_for_plate.raw: %s", _exc)

    if plate_v is None:
        try:
            raw_bytes = getattr(xobj, "_data", None) or xobj.get_data()
            with Image.open(io.BytesIO(raw_bytes)) as im:
                if im.mode == "CMYK":
                    arr = np.asarray(im, dtype=np.uint8)
                    plate_v = arr[:, :, ch_idx].astype(np.float32) / 255.0
                elif im.mode == "L":
                    arr = np.asarray(im, dtype=np.uint8)
                    plate_v = (1.0 - arr.astype(np.float32) / 255.0) if plate_ch == "k" else np.zeros(arr.shape, dtype=np.float32)
                else:
                    rgb = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
                    k = 1.0 - rgb.max(axis=2)
                    if plate_ch == "k":
                        plate_v = k
                    else:
                        rgb_i = {"c": 0, "m": 1, "y": 2}[plate_ch]
                        denom = np.maximum(1e-5, 1.0 - k)
                        plate_v = np.where(k >= 0.999, 0.0, (1.0 - rgb[:, :, rgb_i] - k) / denom)
        except Exception as _exc:
            logging.getLogger("zpdf").debug("preflight._separate_image_xobject_for_plate.pil: %s", _exc)
            return

    plate_v = np.clip(plate_v, 0.0, 1.0)
    if not tinted or plate_ch == "k":
        g8 = np.round((1.0 - plate_v) * 255.0).astype(np.uint8)
        rgb_out = np.stack([g8, g8, g8], axis=2)
    elif plate_ch == "c":
        rgb_out = np.stack([
            np.round((1.0 - plate_v) * 255.0).astype(np.uint8),
            np.round((1.0 - 0.16 * plate_v) * 255.0).astype(np.uint8),
            np.full_like(plate_v, 255, dtype=np.uint8),
        ], axis=2)
    elif plate_ch == "m":
        rgb_out = np.stack([
            np.full_like(plate_v, 255, dtype=np.uint8),
            np.round((1.0 - plate_v) * 255.0).astype(np.uint8),
            np.round((1.0 - 0.14 * plate_v) * 255.0).astype(np.uint8),
        ], axis=2)
    else:
        rgb_out = np.stack([
            np.full_like(plate_v, 255, dtype=np.uint8),
            np.round((1.0 - 0.04 * plate_v) * 255.0).astype(np.uint8),
            np.round((1.0 - plate_v) * 255.0).astype(np.uint8),
        ], axis=2)

    for drop_key in ("/Filter", "/DecodeParms", "/Decode", "/SMask"):
        if NameObject(drop_key) in xobj:
            del xobj[NameObject(drop_key)]
    xobj.set_data(rgb_out.tobytes())
    xobj[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    xobj[NameObject("/BitsPerComponent")] = NumberObject(8)
    xobj[NameObject("/Width")] = NumberObject(rgb_out.shape[1])
    xobj[NameObject("/Height")] = NumberObject(rgb_out.shape[0])


def _render_pdf_bytes_page(pdf_bytes, dpi):
    import pypdfium2 as pdfium

    with core.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            pg = doc[0]
            scale = core.render_scale(pg, float(dpi) / 72.0)
            pil_img = pg.render(scale=scale).to_pil().convert("RGB")
        finally:
            doc.close()
    return pil_img


def _build_single_plate_pdf_bytes(src_path, password, page_idx0, plate_ch, *, tinted=True):
    """Build a 1-page PDF in memory with vectors and images separated for ``plate_ch``."""
    reader = core.open_pdf(Path(src_path), password=password)
    writer = PdfWriter()
    out_page = writer.add_page(reader.pages[page_idx0])

    resources = out_page.get("/Resources")
    if hasattr(resources, "get_object"):
        resources = resources.get_object()
    if resources and hasattr(resources, "get"):
        xobjs = resources.get("/XObject")
        if hasattr(xobjs, "get_object"):
            xobjs = xobjs.get_object()
        if xobjs and hasattr(xobjs, "items"):
            for xk, xref in list(xobjs.items()):
                xobj_orig = xref.get_object() if hasattr(xref, "get_object") else xref
                if xobj_orig and hasattr(xobj_orig, "get") and str(xobj_orig.get("/Subtype", "")).lstrip("/") == "Image":
                    cloned = DecodedStreamObject()
                    for k, v in xobj_orig.items():
                        cloned[k] = v
                    try:
                        cloned.set_data(xobj_orig.get_data())
                    except Exception as _exc:
                        logging.getLogger("zpdf").debug("preflight._build_single_plate_pdf_bytes.xobj: %s", _exc)
                        continue
                    _separate_image_xobject_for_plate(cloned, plate_ch, tinted=tinted)
                    xobjs[xk] = writer._add_object(cloned)

    try:
        cs = ContentStream(out_page.get_contents(), out_page.pdf)
        new_ops = []
        for operands, operator in cs.operations:
            op = operator.decode("latin1") if isinstance(operator, bytes) else str(operator)
            if op in ("k", "K") and len(operands) >= 4:
                c, m, y, k = (_fnum(v) for v in operands[:4])
                r, g, b = _plate_rgb_values(c, m, y, k, plate_ch, tinted=tinted)
                new_ops.append(([FloatObject(r), FloatObject(g), FloatObject(b)], b"rg" if op == "k" else b"RG"))
            elif op in ("rg", "RG") and len(operands) >= 3:
                rr, gg, bb = (_fnum(v) for v in operands[:3])
                c, m, y, k = _rgb_to_cmyk_tuple(rr, gg, bb)
                r, g, b = _plate_rgb_values(c, m, y, k, plate_ch, tinted=tinted)
                new_ops.append(([FloatObject(r), FloatObject(g), FloatObject(b)], b"rg" if op == "rg" else b"RG"))
            elif op in ("g", "G") and operands:
                gv = max(0.0, min(1.0, _fnum(operands[0], 0.0)))
                r, g, b = _plate_rgb_values(0.0, 0.0, 0.0, 1.0 - gv, plate_ch, tinted=tinted)
                new_ops.append(([FloatObject(r), FloatObject(g), FloatObject(b)], b"rg" if op == "g" else b"RG"))
            else:
                new_ops.append((operands, operator))
        cs.operations = new_ops
        out_page[NameObject("/Contents")] = writer._add_object(cs)
    except Exception as _exc:
        logging.getLogger("zpdf").debug("preflight._build_single_plate_pdf_bytes.stream: %s", _exc)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def render_plate_preview(path, password=None, *, page=1, plate="all", max_tac=300.0, dpi=110):
    """Render a print-production Output Preview PNG for 1-based ``page``.

    ``plate``: ``'all'`` (composite), ``'c'``, ``'m'``, ``'y'``, ``'k'`` (true CMYK
    channel separation), or ``'tac'`` (Total Area Coverage ink-limit heatmap).
    Returns ``(png_bytes, metadata_dict)``.
    """
    import numpy as np
    from PIL import Image

    norm_plate = str(plate or "all").strip().lower()
    if norm_plate not in PLATES:
        raise core.PdfManagerError(f"unknown preview plate: {plate!r} (expected one of {PLATES})")

    src = Path(path)
    reader = core.open_pdf(src, password=password)
    total = len(reader.pages)
    page_idx0 = max(0, min(total - 1, int(page) - 1))
    tac_limit = float(max_tac)

    if norm_plate == "all":
        writer = PdfWriter()
        writer.add_page(reader.pages[page_idx0])
        buf = io.BytesIO()
        writer.write(buf)
        pil_img = _render_pdf_bytes_page(buf.getvalue(), dpi)
        out_buf = io.BytesIO()
        pil_img.save(out_buf, format="PNG")
        return out_buf.getvalue(), {"plate": "all", "page": page_idx0 + 1}

    if norm_plate in ("c", "m", "y", "k"):
        pbytes = _build_single_plate_pdf_bytes(src, password, page_idx0, norm_plate, tinted=True)
        pil_img = _render_pdf_bytes_page(pbytes, dpi)
        out_buf = io.BytesIO()
        pil_img.save(out_buf, format="PNG")
        return out_buf.getvalue(), {"plate": norm_plate, "page": page_idx0 + 1}

    # norm_plate == "tac": sum true C + M + Y + K plates and overlay hot-spots
    plate_arrays = []
    for ch in ("c", "m", "y", "k"):
        pbytes = _build_single_plate_pdf_bytes(src, password, page_idx0, ch, tinted=False)
        im_gray = _render_pdf_bytes_page(pbytes, dpi).convert("L")
        arr = 1.0 - (np.asarray(im_gray, dtype=np.float32) / 255.0)
        plate_arrays.append(np.clip(arr, 0.0, 1.0))

    tac_pct = (plate_arrays[0] + plate_arrays[1] + plate_arrays[2] + plate_arrays[3]) * 100.0
    peak_tac = float(tac_pct.max()) if tac_pct.size else 0.0
    over_mask = tac_pct > tac_limit
    warn_mask = (tac_pct >= max(0.0, tac_limit - 30.0)) & (~over_mask)
    over_pct = float(over_mask.mean() * 100.0) if tac_pct.size else 0.0

    # Dimmed neutral grayscale base so amber & red ink warnings pop clearly
    comp_writer = PdfWriter()
    comp_writer.add_page(reader.pages[page_idx0])
    cbuf = io.BytesIO()
    comp_writer.write(cbuf)
    comp_gray = np.asarray(_render_pdf_bytes_page(cbuf.getvalue(), dpi).convert("L"), dtype=np.float32)
    dim_u8 = np.clip(np.round(255.0 - (255.0 - comp_gray) * 0.28), 0, 255).astype(np.uint8)
    heat = np.stack([dim_u8, dim_u8, dim_u8], axis=2)

    heat[warn_mask] = np.array([255, 170, 0], dtype=np.uint8)
    heat[over_mask] = np.array([235, 32, 56], dtype=np.uint8)

    out_buf = io.BytesIO()
    Image.fromarray(heat, mode="RGB").save(out_buf, format="PNG")
    return out_buf.getvalue(), {
        "plate": "tac",
        "page": page_idx0 + 1,
        "max_tac_pct": round(peak_tac, 1),
        "over_limit_pct": round(over_pct, 2),
    }


def report(result, name=""):
    """Format a human-readable print preflight report."""
    prof = result.get("profile", "standard")
    prof_tag = f" [{prof.upper()}]" if prof != "standard" else ""
    header = f"Print preflight{prof_tag}: {name}" if name else f"Print preflight{prof_tag}"
    s = result["summary"]
    status = "PASSED" if result["passed"] else "FAILED"
    tac_note = f"; peak TAC {result['max_tac_pct']:.0f}%" if result.get("max_tac_pct") else ""
    lines = [
        f"{header} — {status} ({result['pages']} pages; {s['errors']} errors, {s['warnings']} warnings{tac_note})",
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
