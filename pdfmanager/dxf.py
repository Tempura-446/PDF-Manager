# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""PDF ↔ DXF conversion (and DWG through an optional ODA File Converter).

- ``pdf_to_dxf`` reads vector paths, solid fills and text from PDF content
  streams (including Form XObjects and ``/OC`` layers), fits straight runs
  and circular arcs, applies the page's drawing scale (``/VP /Measure`` or
  an explicit scale), and writes an AutoCAD-compatible DXF via ``ezdxf``.
- ``dxf_to_pdf`` renders a DXF's model space or paper-space layout onto a
  chosen paper size and scale, preserving DXF layers as PDF optional content
  groups (``/OCProperties``) and writing ``/VP /Measure`` so ZPDF measures
  the result in real units right away.
- ``dwg_to_pdf`` / ``drawing_to_pdf`` converts ``.dwg`` files through the
  user's own ODA File Converter if installed, or explains how to get it.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import ezdxf
from ezdxf import colors as dxf_colors
import pikepdf
from pikepdf import Array, Dictionary, Name, Stream

from pdfmanager import core, update, viewports

# Paper sizes in PDF points (portrait width, height; auto-rotated to match drawing).
PAPERS = {
    "A4": (595.2756, 841.8898),
    "A3": (841.8898, 1190.5512),
    "A2": (1190.5512, 1683.7795),
    "A1": (1683.7795, 2383.9370),
    "A0": (2383.9370, 3370.3937),
    "Letter": (612.0, 792.0),
    "Tabloid": (792.0, 1224.0),
    "Arch D": (1728.0, 2592.0),
}

# DXF $INSUNITS codes ↔ unit names and metres per unit.
_UNIT_TO_INSUNITS = {"in": 1, "ft": 2, "mm": 4, "cm": 5, "m": 6}
_INSUNITS_TO_UNIT = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}
_PT_PER_UNIT = {
    "in": 72.0,
    "ft": 72.0 * 12.0,
    "mm": 72.0 / 25.4,
    "cm": 72.0 / 2.54,
    "m": 72.0 / 0.0254,
}
# Standard DXF lineweights in 1/100 mm.
_DXF_LINEWEIGHTS = (
    0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90,
    100, 106, 120, 140, 158, 200, 211,
)

DRAWING_EXTENSIONS = {".dxf", ".dwg"}


def _sanitize_layer(name: str) -> str:
    clean = re.sub(r'[<>/\\\":;?*|=`,]', "_", str(name or "").strip())
    return clean[:200] or "0"


def _nearest_lw(pt_width: float) -> int:
    mm100 = max(0.0, float(pt_width) * (25.4 / 72.0) * 100.0)
    return min(_DXF_LINEWEIGHTS, key=lambda w: abs(w - mm100))


def _rgb_to_int(rgb: tuple[float, float, float]) -> int:
    r = max(0, min(255, int(round(rgb[0] * 255))))
    g = max(0, min(255, int(round(rgb[1] * 255))))
    b = max(0, min(255, int(round(rgb[2] * 255))))
    return dxf_colors.rgb2int((r, g, b))


def _cmyk_to_rgb(c: float, m: float, y: float, k: float) -> tuple[float, float, float]:
    return ((1.0 - c) * (1.0 - k), (1.0 - m) * (1.0 - k), (1.0 - y) * (1.0 - k))


def _parse_scale(scale) -> dict | None:
    """Normalise a scale specification into ``{"ratio": float, "units": str}`` or None."""
    if not scale:
        return None
    if isinstance(scale, dict) and scale.get("ratio"):
        units = str(scale.get("units") or "m").lower()
        if units not in _PT_PER_UNIT:
            units = "m"
        return {"ratio": float(scale["ratio"]), "units": units}
    text = str(scale).strip().lower()
    if not text or text == "fit":
        return None
    m = re.match(r"^1\s*:\s*([0-9]+(?:\.[0-9]+)?)$", text)
    if m:
        return {"ratio": float(m.group(1)), "units": "m"}
    try:
        val = float(text)
        if val > 0:
            return {"ratio": val, "units": "m"}
    except ValueError:
        pass
    raise core.PdfManagerError(f"Unsupported scale {scale!r} (use e.g. '1:100' or 'fit').")


# ---- Matrix math (2D affine [a, b, c, d, e, f]) ----------------------------

def _mat_mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _pt_mul(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


# ---- Curve fitting (cubic Bézier → LINE / ARC / CIRCLE / SPLINE) -----------

def _cubic_point(p0, p1, p2, p3, t):
    u = 1.0 - t
    u2, t2 = u * u, t * t
    return (
        u2 * u * p0[0] + 3 * u2 * t * p1[0] + 3 * u * t2 * p2[0] + t2 * t * p3[0],
        u2 * u * p0[1] + 3 * u2 * t * p1[1] + 3 * u * t2 * p2[1] + t2 * t * p3[1],
    )


def _point_line_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(dx * (a[1] - p[1]) - (a[0] - p[0]) * dy) / length


def _circle_from_3(p0, p1, p2):
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def _fit_cubic(p0, p1, p2, p3, tol):
    """Classify a cubic Bézier as ``('line', ...)``, ``('arc', cx, cy, r, a0, a1)`` or ``('spline', ...)``."""
    if _point_line_dist(p1, p0, p3) <= tol and _point_line_dist(p2, p0, p3) <= tol:
        return ("line", p0, p3)
    mid = _cubic_point(p0, p1, p2, p3, 0.5)
    circ = _circle_from_3(p0, mid, p3)
    if circ is not None:
        cx, cy, r = circ
        if r > tol * 2 and r < 1e7:
            ok = True
            for t in (0.15, 0.3, 0.7, 0.85):
                px, py = _cubic_point(p0, p1, p2, p3, t)
                if abs(math.hypot(px - cx, py - cy) - r) > max(tol, r * 0.004):
                    ok = False
                    break
            if ok:
                a0 = math.degrees(math.atan2(p0[1] - cy, p0[0] - cx)) % 360.0
                am = math.degrees(math.atan2(mid[1] - cy, mid[0] - cx)) % 360.0
                a1 = math.degrees(math.atan2(p3[1] - cy, p3[0] - cx)) % 360.0
                # DXF ARC runs counter-clockwise from start_angle to end_angle.
                ccw_0m = (am - a0) % 360.0
                ccw_01 = (a1 - a0) % 360.0
                if ccw_0m < ccw_01:
                    return ("arc", cx, cy, r, a0, a1)
                return ("arc", cx, cy, r, a1, a0)
    return ("spline", p0, p1, p2, p3)


# ---- Simple /ToUnicode CMap reader for PDF text extraction -----------------

def _parse_tounicode(font_obj) -> dict[int, str]:
    cmap: dict[int, str] = {}
    if not isinstance(font_obj, Dictionary):
        return cmap
    tu = font_obj.get("/ToUnicode")
    if not isinstance(tu, Stream):
        return cmap
    try:
        text = bytes(tu.read_bytes()).decode("latin-1", errors="ignore")
    except Exception:
        return cmap
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src_h, dst_h in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            try:
                cmap[int(src_h, 16)] = bytes.fromhex(dst_h).decode("utf-16-be", errors="ignore")
            except Exception:
                pass
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo_h, hi_h, dst_h in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            try:
                lo, hi, base = int(lo_h, 16), int(hi_h, 16), int(dst_h, 16)
                for code in range(lo, min(hi + 1, lo + 256)):
                    cmap[code] = chr(base + (code - lo))
            except Exception:
                pass
    return cmap


def _decode_pdf_str(raw, cmap: dict[int, str]) -> str:
    if isinstance(raw, pikepdf.String):
        b = bytes(raw)
    elif isinstance(raw, (bytes, bytearray)):
        b = bytes(raw)
    else:
        return str(raw)
    if cmap:
        # Try 2-byte codes if any key > 255 and even length, else 1-byte
        two_byte = any(k > 255 for k in cmap) and len(b) % 2 == 0
        out = []
        if two_byte:
            for i in range(0, len(b), 2):
                code = (b[i] << 8) | b[i + 1]
                out.append(cmap.get(code, chr(code) if 32 <= code < 127 else ""))
        else:
            for byte in b:
                out.append(cmap.get(byte, chr(byte) if 32 <= byte < 127 else ""))
        res = "".join(out)
        if res.strip():
            return res
    try:
        return b.decode("cp1252")
    except Exception:
        return b.decode("latin-1", errors="ignore")


# ---- PDF → DXF -------------------------------------------------------------

def pdf_to_dxf(path, output, pages=None, scale=None, password=None):
    """Export vector geometry, solid fills and text from ``path`` to DXF ``output``.

    ``pages`` is a 1-based list of page numbers (or ``None`` for all pages).
    ``scale`` is an optional ``{"ratio": float, "units": str}`` or ``"1:100"``;
    when omitted, the page's ``/VP /Measure`` scale is used if present, else
    PDF points.
    """
    src = Path(path)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)

    explicit_scale = _parse_scale(scale)
    stored = viewports.stored_scales(src, password)

    try:
        pdf = pikepdf.open(str(src), password=password or "")
    except pikepdf.PasswordError:
        raise core.PdfManagerError(f"wrong password for {src}") from None
    except pikepdf.PdfError as exc:
        raise core.PdfManagerError(f"{src} is not a readable PDF ({exc})") from None

    with pdf:
        total = len(pdf.pages)
        page_nums = list(range(1, total + 1)) if not pages else [p for p in pages if 1 <= p <= total]
        if not page_nums:
            raise core.PdfManagerError("No pages selected for DXF export.")

        # Choose unit system from explicit scale or first selected page's /VP /Measure.
        active_scale = explicit_scale
        if not active_scale:
            for pno in page_nums:
                if pno in stored and stored[pno]:
                    active_scale = {"ratio": stored[pno][0]["ratio"], "units": stored[pno][0]["units"]}
                    break

        if active_scale:
            units = active_scale["units"]
            pt_factor = active_scale["ratio"] / _PT_PER_UNIT[units]
            insunits = _UNIT_TO_INSUNITS.get(units, 0)
        else:
            units = "pt"
            pt_factor = 1.0
            insunits = 0

        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = insunits
        msp = doc.modelspace()
        created_layers: set[str] = {"0"}

        def ensure_layer(name: str, color_int: int | None = None, lw: int | None = None) -> str:
            lname = _sanitize_layer(name)
            if lname not in created_layers:
                dxfattribs = {}
                if color_int is not None:
                    dxfattribs["true_color"] = color_int
                if lw is not None:
                    dxfattribs["lineweight"] = lw
                doc.layers.add(lname, dxfattribs=dxfattribs)
                created_layers.add(lname)
            return lname

        # Check whether document defines OCG layers.
        has_ocg = bool(pdf.Root.get("/OCProperties"))
        tol = 0.05 * pt_factor  # curve fitting tolerance in DXF units

        x_offset_pt = 0.0
        entity_count = 0

        for idx, pno in enumerate(page_nums):
            page = pdf.pages[pno - 1]
            mbox = [float(v) for v in page.mediabox]
            pw = abs(mbox[2] - mbox[0])
            # Shift each page's origin so page 1 starts at (0, 0) and subsequent pages sit to the right.
            base_ctm = (1.0, 0.0, 0.0, 1.0, x_offset_pt - mbox[0], -mbox[1])
            if len(page_nums) > 1:
                x_offset_pt += pw + 36.0  # 0.5 in gap between sheets

            font_cmaps: dict[str, dict[int, str]] = {}

            def load_fonts(resources):
                if not isinstance(resources, Dictionary):
                    return {}
                fonts = resources.get("/Font")
                if not isinstance(fonts, Dictionary):
                    return {}
                local = {}
                for k, v in fonts.items():
                    key = str(k)
                    if key not in font_cmaps:
                        font_cmaps[key] = _parse_tounicode(v)
                    local[key] = font_cmaps[key]
                return local

            def oc_name_of(prop_name, resources) -> str | None:
                if not isinstance(resources, Dictionary):
                    return None
                props = resources.get("/Properties")
                if not isinstance(props, Dictionary):
                    return None
                oc = props.get(prop_name)
                if isinstance(oc, Dictionary):
                    if str(oc.get("/Type", "")) == "/OCG":
                        return str(oc.get("/Name", "")).strip() or None
                    ocgs = oc.get("/OCGs")
                    if isinstance(ocgs, Dictionary):
                        return str(ocgs.get("/Name", "")).strip() or None
                    if isinstance(ocgs, Array) and len(ocgs) > 0 and isinstance(ocgs[0], Dictionary):
                        return str(ocgs[0].get("/Name", "")).strip() or None
                return None

            def walk(stream_obj, resources, init_ctm, init_layer, depth=0):
                nonlocal entity_count
                if depth > 8:
                    return
                local_fonts = load_fonts(resources)
                state = {
                    "ctm": init_ctm,
                    "stroke": (0.0, 0.0, 0.0),
                    "fill": (0.0, 0.0, 0.0),
                    "lw": 1.0,
                }
                stack = []
                oc_stack = [init_layer] if init_layer else []
                subpaths = []  # list of {"pts": [(kind, ...)], "closed": bool}
                cur_sub = None
                cur_pt = (0.0, 0.0)
                in_text = False
                tm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
                tlm = tm
                t_font = None
                t_size = 12.0
                t_leading = 0.0

                def active_layer(rgb, lw_pt):
                    for name in reversed(oc_stack):
                        if name:
                            return ensure_layer(name)
                    if has_ocg:
                        return "0"
                    cint = _rgb_to_int(rgb)
                    lw_code = _nearest_lw(lw_pt)
                    return ensure_layer(f"C_{cint:06X}_{lw_code:03d}", cint, lw_code)

                def to_dxf(x, y):
                    px, py = _pt_mul(state["ctm"], x, y)
                    return (px * pt_factor, py * pt_factor)

                def emit_path(do_stroke: bool, do_fill: bool):
                    nonlocal subpaths, cur_sub, entity_count
                    if not subpaths:
                        return
                    stroke_rgb = state["stroke"]
                    fill_rgb = state["fill"]
                    # Approximate line width scale from CTM
                    ctm = state["ctm"]
                    ctm_scale = math.hypot(ctm[0], ctm[1]) or 1.0
                    lw_pt = state["lw"] * ctm_scale

                    if do_fill:
                        layer = active_layer(fill_rgb, lw_pt)
                        fill_int = _rgb_to_int(fill_rgb)
                        hatch_polys = []
                        for sp in subpaths:
                            pts = []
                            for seg in sp["segs"]:
                                if seg[0] == "L":
                                    if not pts:
                                        pts.append(seg[1])
                                    pts.append(seg[2])
                                elif seg[0] == "C":
                                    _, p0, p1, p2, p3 = seg
                                    if not pts:
                                        pts.append(p0)
                                    for step in range(1, 9):
                                        pts.append(_cubic_point(p0, p1, p2, p3, step / 8.0))
                            if len(pts) >= 3:
                                hatch_polys.append(pts)
                        if hatch_polys:
                            hatch = msp.add_hatch(dxfattribs={"layer": layer, "true_color": fill_int})
                            hatch.set_solid_fill(rgb=(
                                max(0, min(255, int(round(fill_rgb[0] * 255)))),
                                max(0, min(255, int(round(fill_rgb[1] * 255)))),
                                max(0, min(255, int(round(fill_rgb[2] * 255)))),
                            ))
                            for pts in hatch_polys:
                                hatch.paths.add_polyline_path(pts, is_closed=True)
                            entity_count += 1

                    if do_stroke:
                        layer = active_layer(stroke_rgb, lw_pt)
                        stroke_int = _rgb_to_int(stroke_rgb)
                        lw_code = _nearest_lw(lw_pt)
                        attribs = {"layer": layer, "true_color": stroke_int, "lineweight": lw_code}
                        for sp in subpaths:
                            segs = sp["segs"]
                            if not segs:
                                continue
                            # Check for full circle (closed subpath of 4 circular arcs sharing center/radius)
                            if len(segs) == 4 and all(s[0] == "C" for s in segs) and (
                                sp["closed"]
                                or math.hypot(segs[-1][4][0] - segs[0][1][0], segs[-1][4][1] - segs[0][1][1]) <= max(tol * 2, 1e-3)
                            ):
                                fitted = [_fit_cubic(s[1], s[2], s[3], s[4], tol) for s in segs]
                                if all(f[0] == "arc" for f in fitted):
                                    cx0, cy0, r0 = fitted[0][1], fitted[0][2], fitted[0][3]
                                    if all(math.hypot(f[1] - cx0, f[2] - cy0) <= max(tol * 2, r0 * 0.01)
                                           and abs(f[3] - r0) <= max(tol * 2, r0 * 0.01) for f in fitted[1:]):
                                        msp.add_circle((cx0, cy0), r0, dxfattribs=attribs)
                                        entity_count += 1
                                        continue
                            # Check for pure straight closed rectangle/polyline
                            if all(s[0] == "L" for s in segs):
                                if len(segs) == 1 and not sp["closed"]:
                                    msp.add_line(segs[0][1], segs[0][2], dxfattribs=attribs)
                                    entity_count += 1
                                else:
                                    pts = [segs[0][1]] + [s[2] for s in segs]
                                    if sp["closed"] and len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6:
                                        pts.pop()
                                    msp.add_lwpolyline(pts, close=sp["closed"], dxfattribs=attribs)
                                    entity_count += 1
                                continue
                            # Mixed lines and curves
                            for s in segs:
                                if s[0] == "L":
                                    msp.add_line(s[1], s[2], dxfattribs=attribs)
                                    entity_count += 1
                                else:
                                    fit = _fit_cubic(s[1], s[2], s[3], s[4], tol)
                                    if fit[0] == "line":
                                        msp.add_line(fit[1], fit[2], dxfattribs=attribs)
                                    elif fit[0] == "arc":
                                        _, cx, cy, r, a0, a1 = fit
                                        msp.add_arc((cx, cy), r, a0, a1, dxfattribs=attribs)
                                    else:
                                        _, p0, p1, p2, p3 = fit
                                        msp.add_spline([p0, p1, p2, p3], degree=3, dxfattribs=attribs)
                                    entity_count += 1

                    subpaths = []
                    cur_sub = None

                def emit_text(text_str: str):
                    nonlocal entity_count
                    cleaned = text_str.strip()
                    if not cleaned:
                        return
                    full = _mat_mul(tm, state["ctm"])
                    tx, ty = full[4] * pt_factor, full[5] * pt_factor
                    # Text height = font size scaled by vertical text matrix + CTM
                    height = abs(t_size) * math.hypot(full[2], full[3]) * pt_factor
                    if height <= 1e-6:
                        height = max(1.0, abs(t_size)) * pt_factor
                    angle = math.degrees(math.atan2(full[1], full[0])) % 360.0
                    fill_rgb = state["fill"]
                    layer = active_layer(fill_rgb, 0.25)
                    msp.add_text(
                        cleaned,
                        dxfattribs={
                            "layer": layer,
                            "height": height,
                            "rotation": angle,
                            "insert": (tx, ty),
                            "true_color": _rgb_to_int(fill_rgb),
                        },
                    )
                    entity_count += 1

                try:
                    instructions = pikepdf.parse_content_stream(stream_obj)
                except Exception:
                    return

                for operands, operator in instructions:
                    op = str(operator)
                    if op == "q":
                        stack.append(dict(state))
                    elif op == "Q":
                        if stack:
                            state = stack.pop()
                    elif op == "cm" and len(operands) >= 6:
                        m = tuple(float(operands[i]) for i in range(6))
                        state["ctm"] = _mat_mul(m, state["ctm"])
                    elif op == "w" and operands:
                        state["lw"] = float(operands[0])
                    elif op == "RG" and len(operands) >= 3:
                        state["stroke"] = (float(operands[0]), float(operands[1]), float(operands[2]))
                    elif op == "rg" and len(operands) >= 3:
                        state["fill"] = (float(operands[0]), float(operands[1]), float(operands[2]))
                    elif op == "G" and operands:
                        g = float(operands[0])
                        state["stroke"] = (g, g, g)
                    elif op == "g" and operands:
                        g = float(operands[0])
                        state["fill"] = (g, g, g)
                    elif op == "K" and len(operands) >= 4:
                        state["stroke"] = _cmyk_to_rgb(*(float(operands[i]) for i in range(4)))
                    elif op == "k" and len(operands) >= 4:
                        state["fill"] = _cmyk_to_rgb(*(float(operands[i]) for i in range(4)))
                    elif op == "BDC":
                        lname = None
                        if len(operands) >= 2 and str(operands[0]) == "/OC":
                            lname = oc_name_of(operands[1], resources)
                        oc_stack.append(lname)
                    elif op == "BMC":
                        oc_stack.append(None)
                    elif op == "EMC":
                        if oc_stack:
                            oc_stack.pop()
                    elif op == "m" and len(operands) >= 2:
                        cur_pt = to_dxf(float(operands[0]), float(operands[1]))
                        cur_sub = {"start": cur_pt, "segs": [], "closed": False}
                        subpaths.append(cur_sub)
                    elif op == "l" and len(operands) >= 2:
                        p = to_dxf(float(operands[0]), float(operands[1]))
                        if cur_sub is None:
                            cur_sub = {"start": p, "segs": [], "closed": False}
                            subpaths.append(cur_sub)
                        else:
                            cur_sub["segs"].append(("L", cur_pt, p))
                        cur_pt = p
                    elif op == "c" and len(operands) >= 6:
                        p1 = to_dxf(float(operands[0]), float(operands[1]))
                        p2 = to_dxf(float(operands[2]), float(operands[3]))
                        p3 = to_dxf(float(operands[4]), float(operands[5]))
                        if cur_sub is not None:
                            cur_sub["segs"].append(("C", cur_pt, p1, p2, p3))
                        cur_pt = p3
                    elif op == "v" and len(operands) >= 4:
                        p2 = to_dxf(float(operands[0]), float(operands[1]))
                        p3 = to_dxf(float(operands[2]), float(operands[3]))
                        if cur_sub is not None:
                            cur_sub["segs"].append(("C", cur_pt, cur_pt, p2, p3))
                        cur_pt = p3
                    elif op == "y" and len(operands) >= 4:
                        p1 = to_dxf(float(operands[0]), float(operands[1]))
                        p3 = to_dxf(float(operands[2]), float(operands[3]))
                        if cur_sub is not None:
                            cur_sub["segs"].append(("C", cur_pt, p1, p3, p3))
                        cur_pt = p3
                    elif op == "h":
                        if cur_sub is not None:
                            if math.hypot(cur_pt[0] - cur_sub["start"][0], cur_pt[1] - cur_sub["start"][1]) > 1e-6:
                                cur_sub["segs"].append(("L", cur_pt, cur_sub["start"]))
                            cur_sub["closed"] = True
                            cur_pt = cur_sub["start"]
                    elif op == "re" and len(operands) >= 4:
                        x, y, w, h = (float(operands[i]) for i in range(4))
                        p0 = to_dxf(x, y)
                        p1 = to_dxf(x + w, y)
                        p2 = to_dxf(x + w, y + h)
                        p3 = to_dxf(x, y + h)
                        subpaths.append({
                            "start": p0,
                            "segs": [("L", p0, p1), ("L", p1, p2), ("L", p2, p3), ("L", p3, p0)],
                            "closed": True,
                        })
                        cur_sub = None
                        cur_pt = p0
                    elif op == "S":
                        emit_path(True, False)
                    elif op == "s":
                        if cur_sub is not None:
                            if math.hypot(cur_pt[0] - cur_sub["start"][0], cur_pt[1] - cur_sub["start"][1]) > 1e-6:
                                cur_sub["segs"].append(("L", cur_pt, cur_sub["start"]))
                            cur_sub["closed"] = True
                        emit_path(True, False)
                    elif op in ("f", "F", "f*"):
                        emit_path(False, True)
                    elif op in ("B", "B*"):
                        emit_path(True, True)
                    elif op in ("b", "b*"):
                        if cur_sub is not None:
                            if math.hypot(cur_pt[0] - cur_sub["start"][0], cur_pt[1] - cur_sub["start"][1]) > 1e-6:
                                cur_sub["segs"].append(("L", cur_pt, cur_sub["start"]))
                            cur_sub["closed"] = True
                        emit_path(True, True)
                    elif op == "n":
                        subpaths = []
                        cur_sub = None
                    elif op == "BT":
                        in_text = True
                        tm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
                        tlm = tm
                    elif op == "ET":
                        in_text = False
                    elif in_text and op == "Tf" and len(operands) >= 2:
                        t_font = str(operands[0])
                        t_size = float(operands[1])
                    elif in_text and op == "TL" and operands:
                        t_leading = float(operands[0])
                    elif in_text and op == "Tm" and len(operands) >= 6:
                        tm = tuple(float(operands[i]) for i in range(6))
                        tlm = tm
                    elif in_text and op == "Td" and len(operands) >= 2:
                        tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, float(operands[0]), float(operands[1])), tlm)
                        tm = tlm
                    elif in_text and op == "TD" and len(operands) >= 2:
                        t_leading = -float(operands[1])
                        tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, float(operands[0]), float(operands[1])), tlm)
                        tm = tlm
                    elif in_text and op == "T*":
                        tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -t_leading), tlm)
                        tm = tlm
                    elif in_text and op == "Tj" and operands:
                        emit_text(_decode_pdf_str(operands[0], local_fonts.get(t_font or "", {})))
                    elif in_text and op == "TJ" and operands:
                        cmap = local_fonts.get(t_font or "", {})
                        parts = []
                        for item in operands[0]:
                            if isinstance(item, (pikepdf.String, bytes, bytearray)):
                                parts.append(_decode_pdf_str(item, cmap))
                            elif isinstance(item, (int, float)) and float(item) < -200:
                                parts.append(" ")
                        emit_text("".join(parts))
                    elif in_text and op in ("'", '"') and operands:
                        tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -t_leading), tlm)
                        tm = tlm
                        raw_s = operands[-1]
                        emit_text(_decode_pdf_str(raw_s, local_fonts.get(t_font or "", {})))
                    elif op == "Do" and operands and isinstance(resources, Dictionary):
                        xobjs = resources.get("/XObject")
                        if isinstance(xobjs, Dictionary):
                            xo = xobjs.get(operands[0])
                            if isinstance(xo, Stream) and str(xo.get("/Subtype", "")) == "/Form":
                                fm = xo.get("/Matrix")
                                fmat = tuple(float(fm[i]) for i in range(6)) if fm and len(fm) >= 6 else (1, 0, 0, 1, 0, 0)
                                new_ctm = _mat_mul(fmat, state["ctm"])
                                x_layer = None
                                xoc = xo.get("/OC")
                                if isinstance(xoc, Dictionary) and str(xoc.get("/Type", "")) == "/OCG":
                                    x_layer = str(xoc.get("/Name", "")).strip() or None
                                walk(xo, xo.get("/Resources") or resources, new_ctm, x_layer or (oc_stack[-1] if oc_stack else None), depth + 1)

            walk(page, page.get("/Resources"), base_ctm, None)

        doc.saveas(str(out))
        return {"pages": len(page_nums), "entities": entity_count, "units": units, "scale": active_scale}


# ---- DXF → PDF -------------------------------------------------------------

def _entity_rgb(entity, doc, monochrome: bool) -> tuple[float, float, float]:
    if monochrome:
        return (0.0, 0.0, 0.0)
    rgb = None
    if entity.dxf.hasattr("true_color"):
        rgb = dxf_colors.int2rgb(entity.dxf.true_color)
    else:
        aci = entity.dxf.color
        if aci == 256:  # BYLAYER
            layer = doc.layers.get(entity.dxf.layer)
            if layer is not None:
                if layer.dxf.hasattr("true_color"):
                    rgb = dxf_colors.int2rgb(layer.dxf.true_color)
                else:
                    aci = layer.color
        if rgb is None and isinstance(aci, int) and 1 <= aci <= 255:
            rgb = dxf_colors.aci2rgb(aci)
    if not rgb:
        return (0.0, 0.0, 0.0)
    # AutoCAD white (255,255,255) on dark modelspace should draw black on white paper.
    if rgb[0] > 245 and rgb[1] > 245 and rgb[2] > 245:
        return (0.0, 0.0, 0.0)
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)


def _entity_lw_pt(entity, doc) -> float:
    lw = entity.dxf.get("lineweight", -1)
    if lw == -1:  # BYLAYER
        layer = doc.layers.get(entity.dxf.layer)
        if layer is not None:
            lw = layer.dxf.get("lineweight", -3)
    if lw is None or lw < 0:
        return 0.7  # default ~0.25 mm
    return max(0.25, (lw / 100.0) * (72.0 / 25.4))


def _pdf_escape(text: str) -> str:
    return (
        text.encode("cp1252", errors="replace")
        .decode("latin-1")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _iter_render_entities(layout):
    """Yield flat drawable entities, expanding INSERT and DIMENSION blocks."""
    for ent in layout:
        dxftype = ent.dxftype()
        if dxftype in ("INSERT", "DIMENSION"):
            try:
                for virt in ent.virtual_entities():
                    yield virt
            except Exception:
                continue
        else:
            yield ent


def _collect_dxf_geometry(layout, doc, monochrome: bool):
    """Extract drawable primitives per DXF layer and compute the 2D bounding box."""
    by_layer: dict[str, list] = {}
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    def touch(x, y):
        nonlocal min_x, min_y, max_x, max_y
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y

    for ent in _iter_render_entities(layout):
        t = ent.dxftype()
        layer = ent.dxf.get("layer", "0")
        rgb = _entity_rgb(ent, doc, monochrome)
        lw = _entity_lw_pt(ent, doc)
        bucket = by_layer.setdefault(layer, [])

        if t == "LINE":
            s, e = ent.dxf.start, ent.dxf.end
            touch(s.x, s.y)
            touch(e.x, e.y)
            bucket.append(("poly", [(s.x, s.y), (e.x, e.y)], False, rgb, lw))
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in ent.get_points("xy")]
            if len(pts) >= 2:
                for x, y in pts:
                    touch(x, y)
                bucket.append(("poly", pts, bool(ent.closed), rgb, lw))
        elif t == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in ent.vertices]
            if len(pts) >= 2:
                for x, y in pts:
                    touch(x, y)
                bucket.append(("poly", pts, bool(ent.is_closed), rgb, lw))
        elif t == "CIRCLE":
            c, r = ent.dxf.center, float(ent.dxf.radius)
            touch(c.x - r, c.y - r)
            touch(c.x + r, c.y + r)
            pts = [(c.x + r * math.cos(2 * math.pi * i / 48), c.y + r * math.sin(2 * math.pi * i / 48)) for i in range(48)]
            bucket.append(("poly", pts, True, rgb, lw))
        elif t == "ARC":
            c, r = ent.dxf.center, float(ent.dxf.radius)
            a0, a1 = float(ent.dxf.start_angle), float(ent.dxf.end_angle)
            sweep = (a1 - a0) % 360.0 or 360.0
            steps = max(8, int(math.ceil(sweep / 7.5)))
            pts = []
            for i in range(steps + 1):
                ang = math.radians(a0 + sweep * i / steps)
                x, y = c.x + r * math.cos(ang), c.y + r * math.sin(ang)
                touch(x, y)
                pts.append((x, y))
            bucket.append(("poly", pts, False, rgb, lw))
        elif t == "ELLIPSE":
            try:
                pts = [(p.x, p.y) for p in ent.flattening(0.05)]
                if len(pts) >= 2:
                    for x, y in pts:
                        touch(x, y)
                    bucket.append(("poly", pts, False, rgb, lw))
            except Exception:
                pass
        elif t == "SPLINE":
            try:
                pts = [(p.x, p.y) for p in ent.flattening(0.05)]
                if len(pts) >= 2:
                    for x, y in pts:
                        touch(x, y)
                    bucket.append(("poly", pts, False, rgb, lw))
            except Exception:
                pass
        elif t in ("SOLID", "3DFACE"):
            pts = [(ent.dxf.vtx0.x, ent.dxf.vtx0.y), (ent.dxf.vtx1.x, ent.dxf.vtx1.y),
                   (ent.dxf.vtx2.x, ent.dxf.vtx2.y), (ent.dxf.vtx3.x, ent.dxf.vtx3.y)]
            for x, y in pts:
                touch(x, y)
            bucket.append(("fill", pts, rgb))
        elif t == "HATCH":
            for path in ent.paths:
                pts = []
                if hasattr(path, "vertices"):
                    pts = [(v[0], v[1]) for v in path.vertices]
                elif hasattr(path, "edges"):
                    for edge in path.edges:
                        if hasattr(edge, "start"):
                            pts.append((edge.start[0], edge.start[1]))
                        if hasattr(edge, "end"):
                            pts.append((edge.end[0], edge.end[1]))
                if len(pts) >= 3:
                    for x, y in pts:
                        touch(x, y)
                    bucket.append(("fill", pts, rgb))
        elif t == "TEXT":
            ins = ent.dxf.insert
            h = float(ent.dxf.get("height", 2.5) or 2.5)
            rot = float(ent.dxf.get("rotation", 0.0) or 0.0)
            txt = str(ent.dxf.get("text", "") or "")
            if txt:
                touch(ins.x, ins.y)
                touch(ins.x + len(txt) * h * 0.6, ins.y + h)
                bucket.append(("text", (ins.x, ins.y), h, rot, txt, rgb))
        elif t == "MTEXT":
            ins = ent.dxf.insert
            h = float(ent.dxf.get("char_height", 2.5) or 2.5)
            rot = float(ent.dxf.get("rotation", 0.0) or 0.0)
            txt = ent.plain_text()
            if txt:
                touch(ins.x, ins.y)
                touch(ins.x + max(len(line) for line in txt.splitlines()) * h * 0.6, ins.y + h)
                for li, line in enumerate(txt.splitlines()):
                    bucket.append(("text", (ins.x, ins.y - li * h * 1.3), h, rot, line, rgb))

    if min_x == float("inf"):
        min_x, min_y, max_x, max_y = 0.0, 0.0, 100.0, 100.0
    return by_layer, (min_x, min_y, max_x, max_y)


def dxf_to_pdf(path, output, layout=None, paper="A3", scale="fit", monochrome=False):
    """Convert a DXF file to a layered, scaled PDF.

    - ``layout``: ``"Model"`` (default) or a paper-space layout name.
    - ``paper``: key of ``PAPERS`` (default ``"A3"``); oriented to match the drawing.
    - ``scale``: ``"fit"`` or a ratio string like ``"1:100"`` / ``"1:50"``.
    - ``monochrome``: draw all geometry in black.
    """
    src = Path(path)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        dxf_doc = ezdxf.readfile(str(src))
    except Exception as exc:
        raise core.PdfManagerError(f"Could not read DXF file {src.name}: {exc}") from None

    if not layout or layout.lower() == "model":
        space = dxf_doc.modelspace()
    else:
        if layout not in dxf_doc.layouts:
            raise core.PdfManagerError(f"Layout {layout!r} not found in {src.name}.")
        space = dxf_doc.layouts.get(layout)

    by_layer, (min_x, min_y, max_x, max_y) = _collect_dxf_geometry(space, dxf_doc, bool(monochrome))
    dw = max(1e-6, max_x - min_x)
    dh = max(1e-6, max_y - min_y)

    paper_key = paper if paper in PAPERS else "A3"
    pw, ph = PAPERS[paper_key]
    # Auto-orient paper to match drawing aspect ratio.
    if dw > dh and pw < ph:
        pw, ph = ph, pw
    elif dh > dw and pw > ph:
        pw, ph = ph, pw

    margin = 28.3465  # 10 mm
    avail_w = pw - 2 * margin
    avail_h = ph - 2 * margin

    insunits = int(dxf_doc.header.get("$INSUNITS", 0) or 0)
    unit_name = _INSUNITS_TO_UNIT.get(insunits, "m")
    pt_per_unit = _PT_PER_UNIT[unit_name]

    parsed_scale = _parse_scale(scale)
    if parsed_scale:
        ratio = parsed_scale["ratio"]
        # 1 DXF unit (= 1 real unit) draws as (pt_per_unit / ratio) PDF points on paper.
        pt_per_dxf = pt_per_unit / ratio
        scale_label = f"1:{int(ratio) if ratio == int(ratio) else ratio}"
    else:
        pt_per_dxf = min(avail_w / dw, avail_h / dh)
        # Equivalent real-world ratio for /VP /Measure.
        ratio = pt_per_unit / pt_per_dxf
        scale_label = f"1:{round(ratio)}" if ratio >= 1 else f"{round(1/ratio)}:1"

    # Centre drawing on paper.
    draw_w = dw * pt_per_dxf
    draw_h = dh * pt_per_dxf
    ox = (pw - draw_w) / 2.0 - min_x * pt_per_dxf
    oy = (ph - draw_h) / 2.0 - min_y * pt_per_dxf

    def px(x):
        return x * pt_per_dxf + ox

    def py(y):
        return y * pt_per_dxf + oy

    pdf = pikepdf.new()
    # Collect all DXF layers (preserving order; include used layers).
    layer_names = []
    seen_layers = set()
    for lyr in dxf_doc.layers:
        lname = lyr.dxf.name
        if lname in by_layer and lname not in seen_layers:
            layer_names.append(lname)
            seen_layers.add(lname)
    for lname in by_layer:
        if lname not in seen_layers:
            layer_names.append(lname)
            seen_layers.add(lname)

    ocgs = Array()
    on_arr = Array()
    off_arr = Array()
    props_dict = Dictionary()
    stream_chunks: list[str] = ["1 J 1 j\n"]

    for idx, lname in enumerate(layer_names):
        ocg = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=lname))
        ocgs.append(ocg)
        dxf_lyr = dxf_doc.layers.get(lname) if lname in dxf_doc.layers else None
        is_on = True if dxf_lyr is None else (dxf_lyr.is_on() and not dxf_lyr.is_frozen())
        (on_arr if is_on else off_arr).append(ocg)
        oc_key = Name(f"/L{idx}")
        props_dict[oc_key] = ocg

        stream_chunks.append(f"/OC /L{idx} BDC\n")
        for item in by_layer.get(lname, ()):
            kind = item[0]
            if kind == "poly":
                _, pts, closed, rgb, lw = item
                stream_chunks.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} RG {lw:.2f} w\n")
                x0, y0 = px(pts[0][0]), py(pts[0][1])
                stream_chunks.append(f"{x0:.3f} {y0:.3f} m ")
                for x, y in pts[1:]:
                    stream_chunks.append(f"{px(x):.3f} {py(y):.3f} l ")
                stream_chunks.append("s\n" if closed else "S\n")
            elif kind == "fill":
                _, pts, rgb = item
                stream_chunks.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg\n")
                x0, y0 = px(pts[0][0]), py(pts[0][1])
                stream_chunks.append(f"{x0:.3f} {y0:.3f} m ")
                for x, y in pts[1:]:
                    stream_chunks.append(f"{px(x):.3f} {py(y):.3f} l ")
                stream_chunks.append("h f\n")
            elif kind == "text":
                _, (tx, ty), h, rot, txt, rgb = item
                font_pt = max(2.0, h * pt_per_dxf)
                rad = math.radians(rot)
                c, s = math.cos(rad), math.sin(rad)
                stream_chunks.append(
                    f"BT {rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg /F1 {font_pt:.2f} Tf "
                    f"{c:.4f} {s:.4f} {-s:.4f} {c:.4f} {px(tx):.3f} {py(ty):.3f} Tm "
                    f"({_pdf_escape(txt)}) Tj ET\n"
                )
        stream_chunks.append("EMC\n")

    content_bytes = "".join(stream_chunks).encode("latin-1")
    font_dict = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding)
    resources = Dictionary(Font=Dictionary(F1=font_dict), Properties=props_dict)

    # /VP /Measure so ZPDF measures in real units immediately.
    # 1 PDF pt = 1 / pt_per_dxf DXF units (= 1 / pt_per_dxf real units of unit_name).
    per_pt = float(1.0 / pt_per_dxf)
    pw, ph = float(pw), float(ph)
    measure = Dictionary(
        Type=Name.Measure,
        Subtype=Name.RL,
        R=scale_label,
        X=Array([Dictionary(Type=Name.NumberFormat, U=unit_name, C=per_pt, D=100)]),
        D=Array([Dictionary(Type=Name.NumberFormat, U=unit_name, C=1.0, D=100)]),
        A=Array([Dictionary(Type=Name.NumberFormat, U=f"sq {unit_name}", C=1.0, D=100)]),
    )
    vp = Array([Dictionary(Type=Name.Viewport, Name=layout or "Model", BBox=Array([0, 0, pw, ph]), Measure=measure)])

    page_dict = Dictionary(
        Type=Name.Page,
        MediaBox=Array([0, 0, pw, ph]),
        Resources=resources,
        Contents=pdf.make_stream(content_bytes),
        VP=vp,
    )
    pdf.pages.append(pikepdf.Page(page_dict))

    if len(ocgs) > 0:
        d_cfg = Dictionary(Name="Default", BaseState=Name.ON, ON=on_arr, OFF=off_arr, Order=Array(list(ocgs)))
        pdf.Root[Name.OCProperties] = Dictionary(OCGs=ocgs, D=d_cfg)

    pdf.save(str(out))
    pdf.close()
    return {"layers": layer_names, "paper": paper_key, "scale": scale_label, "units": unit_name, "ratio": float(ratio)}


# ---- DWG via optional ODA File Converter -----------------------------------

_ODA_MISSING_MSG = (
    "DWG files require the free ODA File Converter (from opendesign.com) to be "
    "installed, or you can export the drawing as DXF from your CAD program. "
    "If ODA File Converter is already installed, set its path in Settings."
)


def find_oda_converter(custom_path: str | None = None) -> Path | None:
    """Locate ``ODAFileConverter`` if installed, else return ``None``."""
    candidates: list[str] = []
    if custom_path:
        candidates.append(custom_path)
    cfg = update.settings().get("oda_converter")
    if cfg:
        candidates.append(str(cfg))
    for name in ("ODAFileConverter.exe", "ODAFileConverter"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    # Standard Windows / Linux / macOS install locations
    for base in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ODA",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "ODA",
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path("/Applications/ODAFileConverter.app/Contents/MacOS"),
    ):
        if base.is_dir():
            for hit in sorted(base.rglob("ODAFileConverter*")):
                if hit.is_file() and hit.name in ("ODAFileConverter.exe", "ODAFileConverter"):
                    candidates.append(str(hit))
    for c in candidates:
        p = Path(c)
        if p.is_file():
            return p
    return None


def dwg_to_dxf(path, output, oda_path: str | None = None) -> Path:
    """Convert a ``.dwg`` file to ``.dxf`` using the user's ODA File Converter."""
    converter = find_oda_converter(oda_path)
    if not converter:
        raise core.PdfManagerError(_ODA_MISSING_MSG)
    src = Path(path).resolve()
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zpdf_oda_") as tmp:
        in_dir = Path(tmp) / "in"
        out_dir = Path(tmp) / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        shutil.copy2(src, in_dir / "drawing.dwg")
        cmd = [str(converter), str(in_dir), str(out_dir), "ACAD2018", "DXF", "0", "1", "drawing.dwg"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except OSError as exc:
            raise core.PdfManagerError(f"Could not run ODA File Converter: {exc}") from None
        if res.returncode != 0:
            raise core.PdfManagerError(f"ODA File Converter failed: {(res.stderr or res.stdout).strip()}")
        produced = list(out_dir.glob("*.dxf"))
        if not produced:
            raise core.PdfManagerError("ODA File Converter did not produce a DXF file.")
        shutil.copy2(produced[0], out)
    return out


def drawing_to_pdf(path, output, layout=None, paper="A3", scale="fit", monochrome=False, oda_path: str | None = None):
    """Convert a ``.dxf`` or ``.dwg`` drawing to a layered, scaled PDF."""
    src = Path(path)
    ext = src.suffix.lower()
    if ext == ".dwg":
        with tempfile.TemporaryDirectory(prefix="zpdf_dwg_") as tmp:
            tmp_dxf = Path(tmp) / f"{src.stem}.dxf"
            dwg_to_dxf(src, tmp_dxf, oda_path=oda_path)
            return dxf_to_pdf(tmp_dxf, output, layout=layout, paper=paper, scale=scale, monochrome=monochrome)
    if ext == ".dxf":
        return dxf_to_pdf(src, output, layout=layout, paper=paper, scale=scale, monochrome=monochrome)
    raise core.PdfManagerError(f"Unsupported drawing format {ext!r} (expected .dxf or .dwg).")
