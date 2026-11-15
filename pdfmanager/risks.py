# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""What a PDF from someone else can do, and Make safe.

``check`` looks for the parts of a PDF that can act on the computer or
mislead its reader: scripts, actions that start programs, attached files
that can run, forms that send what's typed, a web address opened by
itself, links that show one address and go to another, and pointers at
other files. Each is said in plain words with how much it matters.

``make_safe`` takes exactly those parts out and keeps everything else:
pages, text, comments, form fields with their own totals and formats
(Acrobat's AF... functions, which only work on the form), and attachments
that can't run. Links that mislead stay (there's nothing to put in their
place): the reader shows the real address before opening any link.

ZPDF itself never runs a PDF's scripts or actions. The check is for what
other programs would do with the file, and for what the person may click.
"""

import re
from urllib.parse import urlsplit

from pypdf.generic import ArrayObject, DictionaryObject, StreamObject

from pdfmanager import core

# Extensions Windows (or Office) runs or scripts when opened.
RUNNABLE = frozenset("""
    exe com scr pif cpl msi msp msix appx bat cmd ps1 psm1 vbs vbe js jse wsf wsh hta jar lnk url reg
    dll sys gadget application appref-ms inf scf chm iso img vhd
    docm dotm xlsm xltm xlam pptm potm ppam ppsm sldm xll
    py pyw sh
""".split())
# What looks like a web address in a link's words but is a file's name.
NOT_SITES = frozenset("""
    pdf doc docx xls xlsx ppt pptx txt csv zip rar htm html php asp aspx jpg jpeg png gif svg mp3 mp4 exe
    json xml md js css
""".split())
KINDS = ("scripts", "launch", "attachments", "submit", "opens_site", "hidden_links", "remote")
SEVERITY = {"scripts": "high", "launch": "high", "attachments": "high", "submit": "medium",
            "opens_site": "medium", "hidden_links": "medium", "remote": "low"}
REMOVED = ("scripts", "launch", "submit", "remote", "attachments", "opens_site")  # what Make safe takes out
ACTION_KIND = {"/JavaScript": "scripts", "/Launch": "launch", "/SubmitForm": "submit",
               "/ImportData": "remote", "/GoToR": "remote", "/GoToE": "remote"}
# A field's script made only of Acrobat's form functions (AFNumber_Format,
# AFSimple_Calculate...): how forms format and add up, here and in Acrobat.
FORM_SCRIPT = re.compile(r"\s*(?:AF\w+\s*\((?:[^()]|\([^()]*\))*\)\s*;?\s*)+")
ADDRESS = re.compile(r"(?<![@\w.-])(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,24})(?![\w-])", re.I)
LINK_PAGES, LINKS = 80, 400  # how far misleading links are looked for
EXAMPLES = 5

# kind: (title, what one does, what several do, (one, several))
WORDS = {
    "scripts": ("It has scripts",
                "{n} that could run when the file is opened or clicked. ZPDF never runs it, but other "
                "programs may.",
                "{n} that could run when the file is opened or clicked. ZPDF never runs them, but other "
                "programs may.", ("script", "scripts")),
    "launch": ("It can start programs",
               "{n} that would start a program or open a file on your computer. ZPDF never does that.",
               "{n} that would start a program or open a file on your computer. ZPDF never does that.",
               ("action", "actions")),
    "attachments": ("It carries files that can run",
                    "{n} that is a program, a script or a document with macros. Don't open it unless you "
                    "trust who sent this.",
                    "{n} that are programs, scripts or documents with macros. Don't open them unless you "
                    "trust who sent this.", ("attached file", "attached files")),
    "submit": ("It can send what you type",
               "{n} that sends what's filled in to an internet address.",
               "{n} that send what's filled in to an internet address.", ("form action", "form actions")),
    "opens_site": ("It opens a web address by itself",
                   "{n} that other programs would open as soon as the file is opened. ZPDF doesn't.",
                   "{n} that other programs would open as soon as the file is opened. ZPDF doesn't.",
                   ("web address", "web addresses")),
    "hidden_links": ("Links that hide where they go",
                     "{n} that shows one web address and goes to another. ZPDF shows the real address "
                     "before opening any link.",
                     "{n} that show one web address and go to another. ZPDF shows the real address before "
                     "opening any link.", ("link", "links")),
    "remote": ("It points at other files",
               "{n} that opens another file or reads data from one.",
               "{n} that open another file or read data from one.", ("action", "actions")),
}
SUMMARY = {
    "scripts": ("script", "scripts"),
    "launch": ("action that runs a program", "actions that run programs"),
    "submit": ("form action that sends what's typed", "form actions that send what's typed"),
    "remote": ("pointer at another file", "pointers at other files"),
    "attachments": ("attachment that could run", "attachments that could run"),
    "opens_site": ("web address opened by itself", "web addresses opened by themselves"),
}


def runnable(name):
    """Whether a file of this name is run (or runs scripts) when opened."""
    return str(name).rsplit(".", 1)[-1].strip().lower() in RUNNABLE if "." in str(name) else False


def summary(removed):
    """What Make safe took out, in words."""
    parts = [f"{n} {SUMMARY[kind][n != 1]}" for kind in REMOVED if (n := removed.get(kind, 0))]
    return ", ".join(parts) if parts else "nothing: it had nothing that could do harm"


def _get(holder, key):
    try:
        value = holder[key]
    except (KeyError, IndexError, TypeError):
        return None
    return value.get_object() if value is not None else None


def _text(value):
    """A string that may be kept as a stream (scripts often are)."""
    value = value.get_object() if value is not None else None
    if isinstance(value, StreamObject):
        try:
            return value.get_data().decode("latin-1")
        except Exception:
            return ""
    return str(value) if value is not None else ""


def _file_name(spec):
    """The name in a file specification (a string, or a dictionary)."""
    spec = spec.get_object() if spec is not None else None
    if isinstance(spec, DictionaryObject):
        for key in ("/UF", "/F", "/Unix", "/DOS", "/Mac"):
            if key in spec:
                return _text(spec[key])
        return ""
    return _text(spec)


def _walk(start, *keys):
    """Every dictionary of a tree (bookmarks, form fields, a name tree),
    once each, from ``start`` along ``keys``."""
    pending, seen = [start], set()
    while pending:
        item = pending.pop()
        item = item.get_object() if item is not None else None
        if isinstance(item, ArrayObject):
            pending.extend(item)
            continue
        if not isinstance(item, DictionaryObject) or id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        pending.extend(item.get(key) for key in keys)


def _name_tree(root_node):
    """(the /Names array, the index of a value in it) for every entry of a
    name tree."""
    for node in _walk(root_node, "/Kids"):
        names = _get(node, "/Names")
        if isinstance(names, ArrayObject):
            for i in range(1, len(names), 2):
                yield names, i


def _sites(root, pages):
    """Every place an action is kept: (holder, key, "field" for a form
    field's own scripts)."""
    if isinstance(_get(root, "/OpenAction"), DictionaryObject):
        yield root, "/OpenAction", "open"

    def additional(holder, where):
        extra = _get(holder, "/AA")
        if isinstance(extra, DictionaryObject):
            for key in list(extra):
                yield extra, key, where

    yield from additional(root, "")
    names = _get(root, "/Names")
    if isinstance(names, DictionaryObject):
        for array, i in _name_tree(_get(names, "/JavaScript")):
            yield array, i, "document"
    for item in _walk(_get(root, "/Outlines"), "/First", "/Next"):
        if "/A" in item:
            yield item, "/A", ""
    acroform = _get(root, "/AcroForm")
    fields = set()
    if isinstance(acroform, DictionaryObject):
        for field in _walk(acroform.get("/Fields"), "/Kids"):
            fields.add(id(field))
            yield from additional(field, "field")
            if "/A" in field:
                yield field, "/A", ""
    for page in pages:
        yield from additional(page, "")
        for annot in _get(page, "/Annots") or []:
            annot = annot.get_object()
            if not isinstance(annot, DictionaryObject) or id(annot) in fields:
                continue
            if "/A" in annot:
                yield annot, "/A", ""
            yield from additional(annot, "field" if annot.get("/Subtype") == "/Widget" else "")


def _judge(action, where):
    """(kind, an example) for an action that matters, else (None, "")."""
    kind = ACTION_KIND.get(action.get("/S"))
    if kind == "scripts":
        script = _text(action.get("/JS"))
        if where == "field" and FORM_SCRIPT.fullmatch(script):
            return None, ""
        return kind, " ".join(script.split())[:80]
    if kind == "launch":
        target = action.get("/F") or _get(action, "/Win") or action.get("/Mac") or action.get("/Unix")
        return kind, _file_name(target)[:120]
    if kind in ("submit", "remote"):
        return kind, _file_name(action.get("/F"))[:160]
    if where == "open" and action.get("/S") == "/URI":
        return "opens_site", _text(action.get("/URI"))[:160]
    return None, ""


def _chain(holder, key, where, seen):
    """(holder, key, action) for the action at holder[key] and every one
    chained behind it with /Next."""
    action = _get(holder, key)
    if not isinstance(action, DictionaryObject) or id(action) in seen:
        return
    seen.add(id(action))
    yield holder, key, action
    after = _get(action, "/Next")
    if isinstance(after, ArrayObject):
        for i in range(len(after)):
            yield from _chain(after, i, "", seen)
    elif after is not None:
        yield from _chain(action, "/Next", "", seen)


def _risky_actions(root, pages):
    """[(holder, key, kind, example)] for every action that matters, each
    once, the ones chained deepest first (so they can be taken out safely)."""
    found, seen = [], set()
    for holder, key, where in _sites(root, pages):
        for at, k, action in _chain(holder, key, where, seen):
            kind, example = _judge(action, where if at is holder else "")
            if kind:
                found.append((at, k, kind, example))
    return found[::-1]


def _attached(root, pages):
    """[(name, how to take it out)] for the files attached to the document
    and to its pages."""
    out = []
    names = _get(root, "/Names")
    if isinstance(names, DictionaryObject):
        for array, i in _name_tree(_get(names, "/EmbeddedFiles")):
            name = _file_name(array[i]) or _text(array[i - 1])
            out.append((name, ("tree", array, i)))
    for page in pages:
        annots = _get(page, "/Annots")
        for ref in annots or []:
            annot = ref.get_object()
            if isinstance(annot, DictionaryObject) and annot.get("/Subtype") == "/FileAttachment":
                out.append((_file_name(annot.get("/FS")), ("annot", annots, annot)))
    return out


def _host(address):
    address = address.strip()
    host = urlsplit(address if "//" in address else "//" + address).hostname or ""
    return host.lower().removeprefix("www.").rstrip(".")


def _misleading(path, password, reader):
    """Links whose words show a web address other than the one they go to:
    ["shows A, goes to B"]."""
    wanted = []
    for number, page in enumerate(reader.pages[:LINK_PAGES]):
        for ref in _get(page, "/Annots") or []:
            annot = ref.get_object()
            if not isinstance(annot, DictionaryObject) or annot.get("/Subtype") != "/Link":
                continue
            action = _get(annot, "/A")
            if not isinstance(action, DictionaryObject) or action.get("/S") != "/URI" or "/Rect" not in annot:
                continue
            target = _text(action.get("/URI"))
            if target.lower().startswith(("http://", "https://")) and _host(target):
                try:
                    rect = [float(v) for v in annot["/Rect"]]
                except (TypeError, ValueError):
                    continue
                wanted.append((number, rect, target))
        if len(wanted) >= LINKS:
            break
    if not wanted:
        return []
    import pypdfium2 as pdfium

    found = []
    try:
        pdf = pdfium.PdfDocument(str(path), password=password or None)
    except Exception:
        return []
    try:
        pages = {}
        for number, (x0, y0, x1, y1), target in wanted[:LINKS]:
            if number not in pages:
                pages[number] = pdf[number].get_textpage()
            try:
                shown = pages[number].get_text_bounded(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            except Exception:
                continue
            goes = _host(target)
            for match in ADDRESS.finditer(shown or ""):
                says = _host(match.group(1))
                if says.rsplit(".", 1)[-1] in NOT_SITES or "." not in says:
                    continue
                if says == goes or goes.endswith("." + says) or says.endswith("." + goes):
                    continue
                found.append(f"shows {match.group(0)}, goes to {goes}")
                break
    finally:
        pdf.close()
    return found


def check(path, password=None):
    """What the PDF at ``path`` can do: {"risks": [{"kind", "severity"
    (high, medium or low), "count", "title", "detail", "examples"}], worst
    first, "level": the worst severity, or "none"}."""
    reader = core.open_pdf(path, password)
    root = reader.trailer["/Root"].get_object()
    counts, examples = {}, {}

    def note(kind, example):
        counts[kind] = counts.get(kind, 0) + 1
        if example and len(examples.setdefault(kind, [])) < EXAMPLES and example not in examples[kind]:
            examples[kind].append(example)

    for _, _, kind, example in _risky_actions(root, reader.pages)[::-1]:
        note(kind, example)
    for name, _ in _attached(root, reader.pages):
        if runnable(name):
            note("attachments", name)
    for example in _misleading(path, password, reader):
        note("hidden_links", example)

    order = {"high": 0, "medium": 1, "low": 2}
    found = []
    for kind in sorted(counts, key=lambda k: (order[SEVERITY[k]], KINDS.index(k))):
        title, single, several, (one, many) = WORDS[kind]
        n = counts[kind]
        found.append({"kind": kind, "severity": SEVERITY[kind], "count": n, "title": title,
                      "detail": (single if n == 1 else several).format(n=f"{n} {one if n == 1 else many}"),
                      "examples": examples.get(kind, [])})
    return {"risks": found, "level": found[0]["severity"] if found else "none"}


def make_safe(path, output, password=None):
    """Write a copy of the PDF without what ``check`` warns about (scripts,
    actions that start programs, send what's typed, open a web address by
    themselves or point at other files, and attachments that can run),
    keeping the rest. Returns how many of each were taken out."""
    reader = core.open_pdf(path, password)
    writer = core.clone_document(reader)
    root = writer._root_object
    removed = dict.fromkeys(REMOVED, 0)
    emptied = []
    for holder, key, kind, _ in _risky_actions(root, writer.pages):
        if isinstance(holder, ArrayObject):
            if isinstance(key, int) and key % 2 == 1 and key >= 1 and not isinstance(_get(holder, key - 1), DictionaryObject):
                emptied.append((holder, key))  # a name tree's pair: name, script
            else:
                del holder[key]
        else:
            del holder[key]
        removed[kind] += 1
    # Name tree pairs go last and from the back, so the earlier indexes hold.
    for holder, key in sorted(emptied, key=lambda item: -item[1]):
        del holder[key - 1:key + 1]
    for name, (how, holder, at) in sorted(_attached(root, writer.pages),
                                          key=lambda item: -(item[1][2] if item[1][0] == "tree" else 0)):
        if not runnable(name):
            continue
        if how == "tree":
            del holder[at - 1:at + 1]
        else:
            for i in range(len(holder) - 1, -1, -1):
                if holder[i].get_object() is at:
                    del holder[i]
        removed["attachments"] += 1
    core._write(writer, output)
    return removed
