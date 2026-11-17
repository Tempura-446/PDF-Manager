# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Stamps of your own: the words, the colour and whether your name and the
date go under them, kept in ZPDF's settings so the reader's Stamp list
offers them next to the built-in ones (markup.py draws them like any
other stamp).

    saved()            -> [{"label", "color", "dated"}]
    save(stamps)       -> the list as kept (checked and tidied)
"""
import re

from pdfmanager import core, update

MOST = 30          # stamps kept
LONGEST = 60       # letters on one
DEFAULT_COLOR = "#d52b1e"


def _one(item):
    """A stamp as it is kept, or a PdfManagerError saying what's wrong."""
    if not isinstance(item, dict):
        raise core.PdfManagerError("a stamp is its words, its colour and whether it's dated")
    label = " ".join(str(item.get("label") or "").split()).upper()
    if not label:
        raise core.PdfManagerError("a stamp needs words, e.g. CHECKED")
    if len(label) > LONGEST:
        raise core.PdfManagerError(f"a stamp's words can be {LONGEST} letters at most")
    color = str(item.get("color") or DEFAULT_COLOR).strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        raise core.PdfManagerError("a stamp's colour is written like #1f8a3b")
    return {"label": label, "color": color, "dated": bool(item.get("dated"))}


def _tidy(items):
    """Checked stamps, the first of each set of words only."""
    seen, out = set(), []
    for item in items:
        stamp = _one(item)
        if stamp["label"] not in seen:
            seen.add(stamp["label"])
            out.append(stamp)
    return out


def saved():
    """The stamps kept in the settings; what can't be read is left out."""
    kept = update.settings().get("stamps")
    out = []
    for item in kept if isinstance(kept, list) else []:
        try:
            stamp = _one(item)
        except core.PdfManagerError:
            continue
        if stamp["label"] not in {s["label"] for s in out}:
            out.append(stamp)
    return out[:MOST]


def save(stamps):
    """Keep ``stamps`` (all of them: what isn't in the list is forgotten)."""
    if not isinstance(stamps, list):
        raise core.PdfManagerError("the stamps come as a list")
    kept = _tidy(stamps)
    if len(kept) > MOST:
        raise core.PdfManagerError(f"ZPDF keeps {MOST} stamps of your own at most; remove one first")
    update.save_settings(stamps=kept or None)
    return kept
