"""ZPDF's licence, and the notices of the software by others it includes or
installs with it.

The notices are built from what is installed: ZPDF's own requirements
(with the "app" and "ocr" extras the Windows installer uses), followed
recursively, each with the licence files its package ships, plus the parts
ZPDF carries itself (pdf.js in static/pdfjs, the interface's fonts in
static/fonts, and Tesseract's glyphless font in textlayer.py). So the list
always matches the copy it comes with.
"""
import re
from importlib import metadata
from pathlib import Path

from pdfmanager import __version__

ROOT = "pdf-manager"
EXTRAS = ("app", "ocr")
HERE = Path(__file__).resolve().parent
_LICENCE_FILE = re.compile(r"licen[cs]e|copying|notice", re.I)


def normal_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _source_requirements(extras):
    """ZPDF's requirements from the pyproject.toml of a source folder, or
    None when this isn't one (or Python is too old to read it). A source
    folder's installed metadata can be older than its pyproject.toml."""
    try:
        import tomllib
    except ImportError:  # Python 3.10
        return None
    try:
        project = tomllib.loads((HERE.parent / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    except (OSError, KeyError, ValueError):
        return None
    specs = list(project.get("dependencies", ()))
    for extra in extras:
        specs += project.get("optional-dependencies", {}).get(extra, ())
    return specs


def distributions(root=ROOT, extras=EXTRAS):
    """The installed packages ``root`` needs with ``extras``, followed
    through their own requirements, without ``root`` itself."""
    from packaging.requirements import Requirement

    found, done, todo = {}, set(), [(root, frozenset(extras))]
    while todo:
        name, wanted = todo.pop()
        key = normal_name(name)
        if (key, wanted) in done:
            continue
        done.add((key, wanted))
        own = _source_requirements(wanted) if key == normal_name(ROOT) else None
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            if own is None:
                continue  # an optional part that isn't installed here
            dist = None
        if dist is not None:
            found.setdefault(key, dist)
        if own is not None:
            todo.extend((Requirement(spec).name, frozenset(Requirement(spec).extras)) for spec in own
                        if not Requirement(spec).marker or Requirement(spec).marker.evaluate({"extra": ""}))
            continue
        for spec in dist.requires or ():
            req = Requirement(spec)
            if req.marker and not any(req.marker.evaluate({"extra": e}) for e in (wanted or {""})):
                continue
            todo.append((req.name, frozenset(req.extras)))
    found.pop(normal_name(root), None)
    return sorted(found.values(), key=lambda d: normal_name(d.metadata["Name"]))


def _licence_line(dist):
    meta = dist.metadata
    text = meta.get("License-Expression") or ""
    if not text:
        licence = (meta.get("License") or "").strip()
        if licence and "\n" not in licence and len(licence) < 120:
            text = licence
    if not text:
        kinds = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or () if c.startswith("License ::")]
        text = ", ".join(kinds)
    return text or "see the licence text below"


def _licence_files(dist):
    files = []
    for file in dist.files or ():
        path = str(file)
        if _LICENCE_FILE.search(Path(path).name) and not path.endswith((".py", ".pyc", ".pyi")):
            files.append(file)
    return files


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


# The interface's fonts, shipped in static/fonts (from the Fontsource packages),
# each with its licence beside it as OFL-<key>.txt.
FONTS = (("Instrument Sans", "instrument-sans"), ("Source Serif 4", "source-serif-4"),
         ("JetBrains Mono", "jetbrains-mono"))


WORDLLAMA = """ZPDF's table of word vectors is a shortened copy (the first 128 of 256 numbers, one
byte each) of the "l2_supercat" model of the WordLlama project,
https://github.com/dleemiller/WordLlama, with that model's vocabulary. WordLlama's
vectors were trained from the token embeddings of large language models that use the
Llama 2 vocabulary.

MIT License

Copyright (c) 2024 Lee Miller

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def _vendored():
    fonts = HERE / "static" / "fonts"
    pdfjs = HERE / "static" / "pdfjs"
    version = _read(pdfjs / "VERSION").split(" ")[0] or "?"
    return [
        ("pdf.js", version, "Apache-2.0 (Mozilla)", _read(pdfjs / "LICENSE")),
        ("Tesseract glyphless font (pdf.ttf, in textlayer.py)", "5", "Apache-2.0 (the Tesseract authors)",
         "Licensed under the Apache License, Version 2.0, as is pdf.js above."),
        ("WordLlama word vectors (static/meaning/words.npz, for search by meaning)", "0.4.0, l2_supercat",
         "MIT (Lee Miller)", WORDLLAMA),
        *((f"{name} font (static/fonts)", "5.3.0 (Fontsource)", "OFL-1.1 (SIL Open Font License 1.1)",
           _read(fonts / f"OFL-{key}.txt")) for name, key in FONTS),
    ]


def third_party_notices():
    """The notices, as one text."""
    parts = [f"ZPDF {__version__} includes or installs the software below, by others, each under its own\n"
             "licence. The licence texts follow each entry.\n"]
    for name, version, licence, text in _vendored():
        parts.append(f"\n== {name} {version} ==\nLicence: {licence}\n\n{text}\n")
    for dist in distributions():
        texts = []
        for file in _licence_files(dist):
            body = _read(dist.locate_file(file))
            if body:
                texts.append(f"--- {Path(str(file)).name} ---\n{body}")
        body = "\n\n".join(texts) or "(this package ships no licence file)"
        parts.append(f"\n== {dist.metadata['Name']} {dist.version} ==\nLicence: {_licence_line(dist)}\n\n{body}\n")
    return "".join(parts)


def licence_text():
    """ZPDF's own licence: from the installed package's metadata, or the
    LICENSE file of a source folder."""
    try:
        dist = metadata.distribution(ROOT)
        for file in dist.files or ():
            if Path(str(file)).name == "LICENSE":
                text = _read(dist.locate_file(file))
                if "PolyForm" in text:
                    return text
    except metadata.PackageNotFoundError:
        pass
    return _read(HERE.parent / "LICENSE") or "ZPDF is shared under the PolyForm Internal Use License 1.0.0."
