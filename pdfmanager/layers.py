"""Layers (optional content) of a PDF: what the reader's Layers tab lists,
saving which layers show when the file opens, and flattening the layers
into the page.

CAD programs export each drawing layer as an optional content group
(OCG). The document catalog's ``/OCProperties`` lists them (``/OCGs``) and
says how they show by default (``/D``: ``/BaseState``, ``/ON``, ``/OFF``,
the panel's nested ``/Order``, ``/Locked`` layers that can't be switched,
and ``/RBGroups``, radio groups in which only one layer is on). Content
belongs to a layer when it's inside a ``/OC /name BDC ... EMC`` sequence
in a content stream, or when an XObject or annotation has an ``/OC`` entry;
either can name an OCG or an optional content membership dictionary (OCMD)
that combines several.

A layer is named here as pdf.js names it, "<object number>R" (with the
generation number after the R when it isn't 0), so the reader and the server
agree on which layer is meant.
"""

import pikepdf
from pikepdf import Array, Dictionary, Name

from pdfmanager import core

# Operators that paint a path (and so end it); "n" ends one without painting.
_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
_PATH = {"m", "l", "c", "v", "y", "h", "re"}
_CLIP = {"W", "W*"}
_SHOW_TEXT = {"Tj", "TJ", "'", '"'}


def layer_id(obj):
    """pdf.js's name for an indirect object: "12R", or "12R3" for generation 3."""
    num, gen = obj.objgen
    return f"{num}R" if gen == 0 else f"{num}R{gen}"


def _open(path, password):
    try:
        return pikepdf.open(str(path), password=password or "")
    except pikepdf.PasswordError:
        raise core.PdfManagerError(f"wrong password for {path}") from None
    except pikepdf.PdfError as exc:
        raise core.PdfManagerError(f"{path} is not a readable PDF ({exc})") from None


def _file_name(path):
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def _text(value):
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - a name that isn't text
        return ""


def _groups(pdf):
    """{objgen: OCG dictionary} of the document's layers, in /OCGs order."""
    props = pdf.Root.get("/OCProperties")
    if not isinstance(props, Dictionary):
        return {}
    found = {}
    for ocg in props.get("/OCGs", Array()):
        if isinstance(ocg, Dictionary) and ocg.is_indirect and ocg.objgen not in found:
            found[ocg.objgen] = ocg
    return found


def _default_config(pdf):
    props = pdf.Root.get("/OCProperties")
    d = props.get("/D") if isinstance(props, Dictionary) else None
    return d if isinstance(d, Dictionary) else Dictionary()


def default_state(pdf):
    """{objgen: shown} as the file opens (its /D configuration)."""
    groups, d = _groups(pdf), _default_config(pdf)
    on = str(d.get("/BaseState", "/ON")) != "/OFF"
    state = {key: on for key in groups}
    for key, shown in (("/ON", True), ("/OFF", False)):
        for ocg in d.get(key, Array()):
            if isinstance(ocg, Dictionary) and ocg.is_indirect and ocg.objgen in state:
                state[ocg.objgen] = shown
    return state


def _state_from_hidden(pdf, hidden):
    """{objgen: shown}: every layer shown except the ones named in ``hidden``
    (pdf.js ids, or layer names)."""
    hidden = {str(h) for h in hidden}
    return {key: not (layer_id(ocg) in hidden or _text(ocg.get("/Name", "")) in hidden)
            for key, ocg in _groups(pdf).items()}


def info(path, password=None):
    """What the reader's Layers tab shows: {"layers": [{id, name}], "order":
    tree, "locked": [ids], "rbgroups": [[ids]], "off": [ids]}. The tree's
    items are {"id", "name", "children"} for a layer and {"label",
    "children"} for a heading; it is the file's /Order, or every layer in
    turn when it has none. Empty when the file has no layers."""
    with _open(path, password) as pdf:
        groups = _groups(pdf)
        if not groups:
            return {"layers": [], "order": [], "locked": [], "rbgroups": [], "off": []}
        d = _default_config(pdf)
        listed = set()

        def item(ocg):
            listed.add(ocg.objgen)
            return {"id": layer_id(ocg), "name": _text(ocg.get("/Name", "")), "children": []}

        def tree(array, depth=0):
            out = []
            if depth > 10 or not isinstance(array, Array):
                return out
            entries = list(array)
            for i, entry in enumerate(entries):
                if isinstance(entry, Dictionary) and entry.is_indirect and entry.objgen in groups:
                    if entry.objgen not in listed:
                        out.append(item(entry))
                elif isinstance(entry, Array):
                    first = entry[0] if len(entry) else None
                    if isinstance(first, pikepdf.String):  # a heading over its layers
                        children = tree(Array(list(entry)[1:]), depth + 1)
                        if children:
                            out.append({"label": _text(first), "children": children})
                    else:
                        children = tree(entry, depth + 1)
                        # An array right after a layer: the layers nested under it.
                        if out and "id" in out[-1] and i and isinstance(entries[i - 1], Dictionary):
                            out[-1]["children"].extend(children)
                        else:
                            out.extend(children)
            return out

        order = tree(d.get("/Order"))
        order += [item(ocg) for key, ocg in groups.items() if key not in listed]
        ids = lambda arr: [layer_id(o) for o in arr if isinstance(o, Dictionary)  # noqa: E731
                           and o.is_indirect and o.objgen in groups]
        rb = [g for g in (ids(a) for a in d.get("/RBGroups", Array()) if isinstance(a, Array)) if g]
        state = default_state(pdf)
        return {"layers": [{"id": layer_id(o), "name": _text(o.get("/Name", ""))} for o in groups.values()],
                "order": order, "locked": ids(d.get("/Locked", Array())), "rbgroups": rb,
                "off": [layer_id(groups[k]) for k, shown in state.items() if not shown]}


def _visible(oc, state, depth=0):
    """Whether content marked with ``oc`` (an OCG or an OCMD) shows."""
    if depth > 20 or not isinstance(oc, Dictionary):
        return True
    if str(oc.get("/Type", "")) == "/OCMD" or "/OCGs" in oc or "/VE" in oc:
        ve = oc.get("/VE")
        if isinstance(ve, Array):
            return _expression(ve, state, depth + 1)
        ocgs = oc.get("/OCGs")
        members = list(ocgs) if isinstance(ocgs, Array) else [ocgs] if ocgs is not None else []
        shown = [_visible(m, state, depth + 1) for m in members if isinstance(m, Dictionary)]
        if not shown:
            return True
        policy = str(oc.get("/P", "/AnyOn"))
        return {"/AllOn": all(shown), "/AnyOff": not all(shown), "/AllOff": not any(shown)}.get(policy, any(shown))
    return state.get(oc.objgen, True) if oc.is_indirect else True


def _expression(ve, state, depth):
    if depth > 20 or not len(ve):
        return True
    op = str(ve[0])
    values = [(_expression(v, state, depth + 1) if isinstance(v, Array) else _visible(v, state, depth + 1))
              for v in list(ve)[1:]]
    if op == "/Not":
        return not values[0] if values else True
    if op == "/And":
        return all(values)
    if op == "/Or":
        return any(values)
    return True


class _Flattener:
    """Takes hidden layers' content out of content streams, and the marks of
    layers out of everything else."""

    def __init__(self, pdf, state):
        self.pdf, self.state = pdf, state
        self.forms = set()  # form XObjects done (each once)

    def visible(self, oc):
        return _visible(oc, self.state)

    def _props(self, operand, resources):
        if isinstance(operand, Dictionary):
            return operand
        props = resources.get("/Properties") if isinstance(resources, Dictionary) else None
        if isinstance(props, Dictionary) and isinstance(operand, Name) and operand in props:
            return props[operand]
        return None

    def rewrite(self, instructions, resources):
        """The instructions without what hidden layers draw, and without the
        marks of the layers that show. Inside a hidden layer, operators that
        change the graphics state stay (they go on mattering after it);
        painting, text and pictures go, and paths unless they clip."""
        out, marks, hidden, path = [], [], 0, []
        xobjects = resources.get("/XObject") if isinstance(resources, Dictionary) else None
        for item in instructions:
            if isinstance(item, pikepdf.ContentStreamInlineImage):
                if not hidden:
                    out.append(item)
                continue
            operands, op = item.operands, str(item.operator)
            if op in ("BMC", "BDC"):
                if hidden:
                    hidden += 1
                    continue
                if op == "BDC" and len(operands) >= 2 and str(operands[0]) == "/OC":
                    props = self._props(operands[1], resources)
                    if not self.visible(props):
                        hidden = 1
                        continue
                    marks.append(False)  # its EMC goes too
                    continue
                marks.append(True)
                out.append(item)
                continue
            if op == "EMC":
                if hidden:
                    hidden -= 1
                    path = []
                    continue
                if not marks or marks.pop():
                    out.append(item)
                continue
            if op == "Do" and operands:
                xobj = xobjects.get(operands[0]) if isinstance(xobjects, Dictionary) else None
                if hidden or (isinstance(xobj, pikepdf.Stream) and "/OC" in xobj and not self.visible(xobj.get("/OC"))):
                    continue
                if isinstance(xobj, pikepdf.Stream) and str(xobj.get("/Subtype", "")) == "/Form":
                    self.form(xobj, resources)
                out.append(item)
                continue
            if not hidden:
                out.append(item)
                continue
            # Inside a hidden layer.
            if op in _PATH or op in _CLIP:
                path.append(item)
            elif op in _PAINT:
                if any(str(p.operator) in _CLIP for p in path):
                    out += path
                    out.append(([], pikepdf.Operator("n")))
                path = []
            elif op in _SHOW_TEXT or op in ("sh", "MP", "DP"):
                if op == "'":
                    out.append(([], pikepdf.Operator("T*")))
                elif op == '"' and len(operands) == 3:
                    out += [([operands[0]], pikepdf.Operator("Tw")), ([operands[1]], pikepdf.Operator("Tc")),
                            ([], pikepdf.Operator("T*"))]
            else:
                out.append(item)  # q, Q, cm, colours, text state and position...
        return out

    def form(self, xobj, outer):
        if xobj.objgen in self.forms:
            return
        self.forms.add(xobj.objgen)
        resources = xobj.get("/Resources")
        if not isinstance(resources, Dictionary):
            resources = outer
        try:
            instructions = pikepdf.parse_content_stream(xobj)
        except pikepdf.PdfError:
            return
        xobj.write(pikepdf.unparse_content_stream(self.rewrite(instructions, resources)))
        self.unmark(resources)

    def unmark(self, resources):
        """Layer marks off a resource dictionary's XObjects and properties."""
        if not isinstance(resources, Dictionary):
            return
        xobjects = resources.get("/XObject")
        if isinstance(xobjects, Dictionary):
            for key in list(xobjects.keys()):
                xobj = xobjects[key]
                if isinstance(xobj, pikepdf.Stream) and "/OC" in xobj:
                    if self.visible(xobj.get("/OC")):
                        del xobj["/OC"]
                    else:  # (never drawn now: Do of it was taken out)
                        del xobjects[key]
        props = resources.get("/Properties")
        if isinstance(props, Dictionary):
            for key in list(props.keys()):
                value = props[key]
                if isinstance(value, Dictionary) and str(value.get("/Type", "")) in ("/OCG", "/OCMD"):
                    del props[key]

    def page(self, page):
        resources = page.obj.get("/Resources")
        if not isinstance(resources, Dictionary):
            resources = Dictionary()
        instructions = pikepdf.parse_content_stream(page)
        page.obj.Contents = self.pdf.make_stream(pikepdf.unparse_content_stream(self.rewrite(instructions, resources)))
        annots = page.obj.get("/Annots")
        if isinstance(annots, Array):
            kept = Array()
            for annot in annots:
                if isinstance(annot, Dictionary) and "/OC" in annot:
                    if not self.visible(annot.get("/OC")):
                        continue
                    del annot["/OC"]
                if isinstance(annot, Dictionary):
                    for appearance in self._appearances(annot):
                        self.form(appearance, resources)
                kept.append(annot)
            page.obj.Annots = kept
        self.unmark(resources)

    @staticmethod
    def _appearances(annot):
        ap = annot.get("/AP")
        if not isinstance(ap, Dictionary):
            return []
        found = []
        for key in ("/N", "/R", "/D"):
            entry = ap.get(key)
            if isinstance(entry, pikepdf.Stream):
                found.append(entry)
            elif isinstance(entry, Dictionary):
                found += [s for s in entry.values() if isinstance(s, pikepdf.Stream)]
        return found


def _names(value):
    if isinstance(value, str):
        value = [value]
    return [n.strip() for item in value or [] for n in str(item).split(",") if n.strip()]


def hidden_names(path, hide=None, show=None, password=None):
    """The layers hidden as the file opens, with ``hide`` hidden too and
    ``show`` shown (names: a list, or a text with commas), as pdf.js ids.
    Raises PdfManagerError for a name the file has no layer of."""
    hide, show = _names(hide), _names(show)
    with _open(path, password) as pdf:
        groups = _groups(pdf)
        names = {_text(o.get("/Name", "")) for o in groups.values()}
        unknown = [n for n in hide + show if n not in names]
        if unknown:
            raise core.PdfManagerError(f"{_file_name(path)} has no layer called " + ", ".join(
                f"“{n}”" for n in unknown) + (f" (its layers: {', '.join(sorted(names))})" if names else ""))
        state = default_state(pdf)
        hidden = []
        for key, ocg in groups.items():
            name = _text(ocg.get("/Name", ""))
            if name in hide or (not state[key] and name not in show):
                hidden.append(layer_id(ocg))
        return hidden


def flatten_layers(path, output, hidden=None, password=None):
    """Write ``output``: ``path`` with the layers that show (as the file
    opens, or every layer but the ones in ``hidden``: pdf.js ids or layer
    names) part of the page and the hidden layers' content removed; the
    file has no layers after. Returns {"kept": [names], "removed": [names]};
    raises PdfManagerError when it has no layers."""
    with _open(path, password) as pdf:
        groups = _groups(pdf)
        if not groups:
            raise core.PdfManagerError(f"{_file_name(path)} has no layers to flatten")
        state = default_state(pdf) if hidden is None else _state_from_hidden(pdf, hidden)
        work = _Flattener(pdf, state)
        for page in pdf.pages:
            work.page(page)
        del pdf.Root["/OCProperties"]
        names = {key: _text(ocg.get("/Name", "")) for key, ocg in groups.items()}
        core.keep_size(pdf)
        pdf.save(str(output))
    return {"kept": [names[k] for k, shown in state.items() if shown],
            "removed": [names[k] for k, shown in state.items() if not shown]}


def save_visibility(path, output, hidden, password=None):
    """Write ``output``: ``path`` opening with every layer shown except the
    ones in ``hidden`` (pdf.js ids or layer names), the way Acrobat's "Save
    layer visibility" does: the default configuration's /ON and /OFF.
    Locked layers keep their state. Returns the names of the hidden layers."""
    with _open(path, password) as pdf:
        groups = _groups(pdf)
        if not groups:
            raise core.PdfManagerError(f"{_file_name(path)} has no layers")
        props = pdf.Root.OCProperties
        d = props.get("/D")
        if not isinstance(d, Dictionary):
            d = props.D = pdf.make_indirect(Dictionary())
        before = default_state(pdf)
        state = _state_from_hidden(pdf, hidden)
        locked = {o.objgen for o in d.get("/Locked", Array()) if isinstance(o, Dictionary) and o.is_indirect}
        for key in locked:
            if key in state:
                state[key] = before.get(key, True)
        d.BaseState = Name.ON
        d.ON = Array([groups[k] for k, shown in state.items() if shown])
        d.OFF = Array([groups[k] for k, shown in state.items() if not shown])
        core.keep_size(pdf)
        pdf.save(str(output))
        return [_text(groups[k].get("/Name", "")) for k, shown in state.items() if not shown]
