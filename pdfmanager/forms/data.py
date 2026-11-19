# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Reading, filling, clearing, importing/exporting (FDF/XFDF/CSV/JSON) and batch-filling forms."""

import json
import re

from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

from pdfmanager import core
from ._base import _inherited, _key, _kind, _qualified_name, _widgets

def _set_field_value(writer, field, widgets, kind, val):
    """Apply ``val`` to ``field`` (and its ``widgets``) in ``writer``."""
    if kind in ("checkbox", "radio"):
        states = set()
        for w in widgets:
            n = (w.get("/AP") or {}).get("/N")
            n = n.get_object() if n is not None else None
            if isinstance(n, dict):
                states.update(str(k)[1:] for k in n.keys() if str(k) != "/Off")
        if kind == "checkbox":
            exp = next(iter(states), "Yes")
            on = False
            if isinstance(val, bool):
                on = val
            elif val is not None:
                s = str(val).strip()
                on = bool(s) and s.lower() not in ("off", "false", "0", "no")
            target = NameObject("/" + exp) if on else NameObject("/Off")
        else:
            s = "" if val is None else str(val).strip()
            target = NameObject("/" + s) if s and s.lower() != "off" else NameObject("/Off")
        field[NameObject("/V")] = target
        for w in widgets:
            n = (w.get("/AP") or {}).get("/N")
            n = n.get_object() if n is not None else None
            if isinstance(n, dict) and str(target) in n:
                w[NameObject("/AS")] = target
            else:
                w[NameObject("/AS")] = NameObject("/Off")
        return

    if kind in ("dropdown", "list") and isinstance(val, (list, tuple)):
        arr = ArrayObject([TextStringObject(str(x)) for x in val if x is not None and str(x) != ""])
        if arr:
            field[NameObject("/V")] = arr
        elif "/V" in field:
            del field["/V"]
    else:
        s = "" if val is None else str(val)
        if s == "":
            if "/V" in field:
                del field["/V"]
        else:
            field[NameObject("/V")] = TextStringObject(s)
    if "/I" in field:
        del field["/I"]
    for w in widgets:
        if "/AP" in w:
            del w["/AP"]
    acro = writer._root_object.get("/AcroForm")
    if acro is not None:
        acro.get_object()[NameObject("/NeedAppearances")] = BooleanObject(True)


def clear_form(path, output, fields=None, password=None):
    """Reset form fields to their default values (``/DV``), or clear them."""
    wanted = set(fields) if fields is not None else None
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    by_fid = {}
    for _, _, annot, field in _widgets(writer.pages):
        fid = _key(field)
        by_fid.setdefault(fid, [field, []])[1].append(annot)

    count = 0
    for field, widgets in by_fid.values():
        name = _qualified_name(field)
        if wanted is not None and name not in wanted:
            continue
        kind = _kind(field)
        if kind in ("signature", "button"):
            continue
        dv = _inherited(field, "/DV")
        dv = dv.get_object() if dv is not None else None
        if dv is not None:
            if isinstance(dv, NameObject):
                val = "" if str(dv) == "/Off" else str(dv)[1:]
            elif isinstance(dv, (list, tuple)):
                val = [str(x) for x in dv]
            else:
                val = str(dv)
            _set_field_value(writer, field, widgets, kind, val)
        else:
            _set_field_value(writer, field, widgets, kind, "")
        count += 1
    core._write(writer, output)
    return count


def read_form_values(path, password=None):
    """Return an ordered dict ``{field_name: value}`` of filled values."""
    reader = core.open_pdf(path, password)
    out = {}
    for _, _, _, field in _widgets(reader.pages):
        name = _qualified_name(field)
        if not name or name in out:
            continue
        kind = _kind(field)
        if kind in ("signature", "button"):
            continue
        raw = _inherited(field, "/V")
        raw = raw.get_object() if raw is not None else None
        if raw is None:
            out[name] = ""
        elif isinstance(raw, NameObject):
            out[name] = "" if str(raw) == "/Off" else str(raw)[1:]
        elif isinstance(raw, (list, tuple)):
            out[name] = [str(x) for x in raw]
        else:
            out[name] = str(raw)
    return out


def fill_form(path, output, values, password=None):
    """Fill form fields from ``values`` (``{name: value}``)."""
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    by_fid = {}
    for _, _, annot, field in _widgets(writer.pages):
        fid = _key(field)
        by_fid.setdefault(fid, [field, []])[1].append(annot)

    count = 0
    for field, widgets in by_fid.values():
        name = _qualified_name(field)
        if not name or name not in values:
            continue
        kind = _kind(field)
        if kind in ("signature", "button"):
            continue
        _set_field_value(writer, field, widgets, kind, values[name])
        count += 1
    core._write(writer, output)
    return count


def _fdf_escape(text):
    return (str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            .replace("\r", "\\r").replace("\n", "\\n"))


def export_data(path, output, fmt="json", password=None):
    """Export filled form values to ``output`` as FDF, XFDF, CSV, or JSON."""
    import csv
    import xml.etree.ElementTree as ET
    from pathlib import Path

    fmt = (fmt or "json").lower().lstrip(".")
    values = read_form_values(path, password=password)
    out_path = Path(output)

    if fmt == "json":
        out_path.write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif fmt == "csv":
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(list(values.keys()))
            w.writerow(["; ".join(v) if isinstance(v, list) else v for v in values.values()])
    elif fmt == "xfdf":
        root = ET.Element("xfdf", {"xmlns": "http://ns.adobe.com/xfdf/", "xml:space": "preserve"})
        fields_el = ET.SubElement(root, "fields")
        for k, v in values.items():
            f_el = ET.SubElement(fields_el, "field", {"name": k})
            if isinstance(v, list):
                for item in v:
                    v_el = ET.SubElement(f_el, "value")
                    v_el.text = str(item)
            else:
                v_el = ET.SubElement(f_el, "value")
                v_el.text = str(v)
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        out_path.write_bytes(xml_bytes + b"\n")
    elif fmt == "fdf":
        entries = []
        for k, v in values.items():
            if isinstance(v, list):
                vals = "[" + " ".join(f"({_fdf_escape(item)})" for item in v) + "]"
            else:
                vals = f"({_fdf_escape(v)})"
            entries.append(f"<< /T ({_fdf_escape(k)}) /V {vals} >>")
        body = (
            "%FDF-1.2\n1 0 obj\n<< /FDF << /Fields [\n"
            + "\n".join(entries)
            + "\n] >> >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
        )
        out_path.write_bytes(body.encode("latin-1", errors="replace"))
    else:
        raise core.PdfManagerError(f"unsupported data format: {fmt}")
    return len(values)


def _parse_fdf(raw_bytes):
    """Parse ``/T`` and ``/V`` field entries from FDF bytes."""
    text = raw_bytes.decode("latin-1", errors="replace")
    i, n = 0, len(text)

    def skip_ws():
        nonlocal i
        while i < n:
            if text[i] in " \t\r\n\x00\x0c":
                i += 1
            elif text[i] == "%":
                while i < n and text[i] not in "\r\n":
                    i += 1
            else:
                break

    def read_str():
        nonlocal i
        i += 1  # '('
        depth, buf = 1, []
        while i < n and depth > 0:
            ch = text[i]
            if ch == "\\":
                i += 1
                if i >= n:
                    break
                esc = text[i]
                if esc in "nrtbf":
                    buf.append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}[esc])
                elif esc in "01234567":
                    octs = esc
                    for _ in range(2):
                        if i + 1 < n and text[i + 1] in "01234567":
                            i += 1
                            octs += text[i]
                        else:
                            break
                    buf.append(chr(int(octs, 8)))
                elif esc in "\r\n":
                    if esc == "\r" and i + 1 < n and text[i + 1] == "\n":
                        i += 1
                else:
                    buf.append(esc)
                i += 1
            elif ch == "(":
                depth += 1
                buf.append(ch)
                i += 1
            elif ch == ")":
                depth -= 1
                if depth > 0:
                    buf.append(ch)
                i += 1
            else:
                buf.append(ch)
                i += 1
        return "".join(buf)

    def read_val():
        nonlocal i
        skip_ws()
        if i >= n:
            return None
        if text[i] == "(":
            return read_str()
        if text.startswith("<<", i):
            i += 2
            d = {}
            while i < n:
                skip_ws()
                if text.startswith(">>", i):
                    i += 2
                    return d
                k = read_val()
                if k is None:
                    break
                v = read_val()
                d[k] = v
            return d
        if text[i] == "[":
            i += 1
            arr = []
            while i < n:
                skip_ws()
                if text[i] == "]":
                    i += 1
                    return arr
                v = read_val()
                if v is None:
                    break
                arr.append(v)
            return arr
        if text[i] == "<":
            end = text.find(">", i + 1)
            hx = re.sub(r"\s+", "", text[i + 1:end]) if end > i else ""
            i = (end + 1) if end >= 0 else n
            if len(hx) % 2:
                hx += "0"
            raw = bytes.fromhex(hx)
            if raw.startswith(b"\xfe\xff"):
                return raw[2:].decode("utf-16-be", errors="replace")
            return raw.decode("latin-1", errors="replace")
        if text[i] == "/":
            i += 1
            start = i
            while i < n and text[i] not in " \t\r\n\x00\x0c/[]<>()":
                i += 1
            return text[start:i]
        start = i
        while i < n and text[i] not in " \t\r\n\x00\x0c/[]<>()":
            i += 1
        return text[start:i]

    out = {}

    def walk(node, prefix=""):
        if isinstance(node, dict):
            name = node.get("T")
            full = f"{prefix}.{name}" if (prefix and name) else (name or prefix)
            if "V" in node and full:
                out[full] = node["V"]
            kids = node.get("Kids")
            if isinstance(kids, list):
                for kid in kids:
                    walk(kid, full)
            fdf = node.get("FDF")
            if isinstance(fdf, dict) and isinstance(fdf.get("Fields"), list):
                for f in fdf["Fields"]:
                    walk(f, "")
            if isinstance(node.get("Fields"), list):
                for f in node["Fields"]:
                    walk(f, "")
        elif isinstance(node, list):
            for item in node:
                walk(item, prefix)

    while i < n:
        skip_ws()
        if i >= n:
            break
        if text.startswith("<<", i) or text[i] == "[":
            walk(read_val())
        else:
            i += 1
    return out


def _parse_data_file(data_path, fmt=None):
    """Read field values from an FDF, XFDF, CSV, or JSON file."""
    import csv
    import xml.etree.ElementTree as ET
    from pathlib import Path

    p = Path(data_path)
    raw = p.read_bytes()
    if not fmt:
        ext = p.suffix.lower().lstrip(".")
        if ext in ("fdf", "xfdf", "csv", "json"):
            fmt = ext
        elif ext == "xml" or raw.lstrip().startswith(b"<"):
            fmt = "xfdf"
        elif raw.lstrip().startswith(b"%FDF"):
            fmt = "fdf"
        elif raw.lstrip().startswith((b"{", b"[")):
            fmt = "json"
        else:
            fmt = "csv"

    if fmt == "json":
        obj = json.loads(raw.decode("utf-8"))
        if isinstance(obj, list):
            obj = obj[0] if obj else {}
        if not isinstance(obj, dict):
            raise core.PdfManagerError("JSON data must be an object mapping field names to values")
        return obj
    if fmt == "csv":
        lines = raw.decode("utf-8-sig").splitlines()
        rows = list(csv.DictReader(lines))
        return dict(rows[0]) if rows else {}
    if fmt == "xfdf":
        root = ET.fromstring(raw.decode("utf-8"))
        out = {}

        def walk_xfdf(el, prefix=""):
            tag = el.tag.split("}")[-1]
            if tag == "field":
                name = el.attrib.get("name", "")
                full = f"{prefix}.{name}" if (prefix and name) else (name or prefix)
                vals = [c.text or "" for c in el if c.tag.split("}")[-1] == "value"]
                if vals and full:
                    out[full] = vals[0] if len(vals) == 1 else vals
                for c in el:
                    if c.tag.split("}")[-1] == "field":
                        walk_xfdf(c, full)
            else:
                for c in el:
                    walk_xfdf(c, prefix)

        walk_xfdf(root)
        return out
    if fmt == "fdf":
        return _parse_fdf(raw)
    raise core.PdfManagerError(f"unsupported data format: {fmt}")


def import_data(path, data_path, output, fmt=None, password=None):
    """Fill form fields in ``path`` from an FDF, XFDF, CSV, or JSON file."""
    values = _parse_data_file(data_path, fmt=fmt)
    return fill_form(path, output, values, password=password)


def fill_many(path, data_path, out_dir, name_column=None, password=None):
    """Mail merge: create one filled PDF per row of a CSV, XLSX, or JSON file."""
    import csv
    from pathlib import Path

    src = Path(path)
    dp = Path(data_path)
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    ext = dp.suffix.lower()

    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(str(dp), read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(it, ())]
        rows = []
        for r in it:
            if not any(v is not None and str(v) != "" for v in r):
                continue
            rows.append({headers[i]: ("" if v is None else str(v))
                         for i, v in enumerate(r) if i < len(headers) and headers[i]})
        wb.close()
    elif ext == ".json":
        data = json.loads(dp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = [data]
        rows = [{str(k): ("" if v is None else v) for k, v in row.items()} for row in data if isinstance(row, dict)]
    else:
        rows = list(csv.DictReader(dp.read_text(encoding="utf-8-sig").splitlines()))

    if not rows:
        raise core.PdfManagerError("the data file has no rows")

    outputs = []
    used_names = set()
    for idx, row in enumerate(rows, 1):
        raw_name = str(row.get(name_column) or "").strip() if name_column else ""
        stem = re.sub(r'[\\/:*?"<>|\r\n]+', "_", raw_name).strip(" ._") if raw_name else f"{src.stem}_{idx}"
        if not stem:
            stem = f"{src.stem}_{idx}"
        candidate = f"{stem}.pdf"
        suffix = 2
        while candidate.lower() in used_names:
            candidate = f"{stem}_{suffix}.pdf"
            suffix += 1
        used_names.add(candidate.lower())
        out_file = dest / candidate
        fill_form(src, out_file, row, password=password)
        outputs.append(out_file)
    return outputs

