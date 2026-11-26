# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Visual Cover-Art Bookshelf library manager and sibling chapter navigation.

Manages user-selected local folders containing PDF, CBZ, CBR, and EPUB books,
generates cached JPEG cover thumbnails, and resolves natural-sorted Next/Prev
chapter neighbors in the same folder.
"""

import hashlib
import io
import json
import logging
import re
import zipfile
from pathlib import Path

from pdfmanager import appdata, cbz, core

BOOK_EXTS = {".pdf", ".cbz", ".cbr", ".epub"}
_LOG = logging.getLogger("zpdf")


def _natural_key(text):
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", str(text))]


def _config_path():
    return appdata.data_dir() / "bookshelf.json"


def _thumbs_dir():
    d = appdata.data_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _book_id(path):
    return hashlib.sha256(str(Path(path).resolve()).encode("utf-8", errors="ignore")).hexdigest()[:16]


def list_folders():
    """Return the saved list of library folder paths."""
    p = _config_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [str(f) for f in (data.get("folders") or []) if isinstance(f, str) and f.strip()]
    except Exception:
        return []


def _save_folders(folders):
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"folders": list(dict.fromkeys(folders))}, indent=2), encoding="utf-8")


def add_folder(folder_path):
    """Add a local directory path to the Bookshelf library."""
    import os

    raw = str(folder_path or "").strip()
    if not raw or "\x00" in raw:
        raise core.PdfManagerError("enter a valid folder path")
    norm = os.path.normpath(os.path.expanduser(raw))
    folders = list_folders()
    if norm not in folders:
        folders.append(norm)
        _save_folders(folders)
    return folders


def remove_folder(folder_path):
    """Remove a directory from the Bookshelf library."""
    raw = str(folder_path or "").strip()
    folders = [f for f in list_folders() if f != raw]
    _save_folders(folders)
    return folders


def scan_library(query="", *, max_items=400):
    """Scan all configured Bookshelf folders and return natural-sorted book items."""
    q = str(query or "").strip().lower()
    items = []
    seen = set()
    for folder in list_folders():
        root = Path(folder)
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir(), key=lambda p: _natural_key(p.name))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if ext not in BOOK_EXTS or entry.name.startswith("."):
                continue
            if q and q not in entry.name.lower() and q not in root.name.lower():
                continue
            resolved = str(entry.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                st = entry.stat()
                size_bytes = int(st.st_size)
                mtime = int(st.st_mtime)
            except OSError:
                continue
            items.append({
                "id": _book_id(resolved),
                "path": resolved,
                "name": entry.name,
                "stem": entry.stem,
                "ext": ext.lstrip(".").upper(),
                "folder": root.name or str(root),
                "size_bytes": size_bytes,
                "mtime": mtime,
            })
            if len(items) >= max_items:
                break
        if len(items) >= max_items:
            break
    return {"folders": list_folders(), "items": items}


def find_by_id(book_id):
    """Resolve a 16-char bookshelf item id back to its Path on disk."""
    for item in scan_library(max_items=2000)["items"]:
        if item["id"] == book_id:
            return Path(item["path"])
    return None


def _render_cover_pil(path, max_w=240):
    from PIL import Image

    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(p))
        try:
            if len(doc) == 0:
                return None
            page = doc[0]
            try:
                pil = page.render(scale=0.5).to_pil().convert("RGB")
            finally:
                page.close()
        finally:
            doc.close()
    elif ext in (".cbz", ".cbr") and zipfile.is_zipfile(p):
        with zipfile.ZipFile(p, "r") as zf:
            imgs = [
                inf for inf in zf.infolist()
                if not inf.is_dir()
                and not Path(inf.filename).name.startswith(".")
                and "__MACOSX" not in inf.filename.split("/")
                and Path(inf.filename).suffix.lower() in cbz.IMAGE_EXTS
            ]
            if not imgs:
                return None
            imgs.sort(key=lambda inf: _natural_key(inf.filename))
            with Image.open(io.BytesIO(zf.read(imgs[0]))) as im:
                pil = im.convert("RGB")
    elif ext == ".epub" and zipfile.is_zipfile(p):
        with zipfile.ZipFile(p, "r") as zf:
            imgs = [
                inf for inf in zf.infolist()
                if not inf.is_dir() and Path(inf.filename).suffix.lower() in cbz.IMAGE_EXTS
            ]
            if not imgs:
                return None
            # Prefer a file with "cover" in its name, otherwise first image
            imgs.sort(key=lambda inf: (0 if "cover" in inf.filename.lower() else 1, _natural_key(inf.filename)))
            with Image.open(io.BytesIO(zf.read(imgs[0]))) as im:
                pil = im.convert("RGB")
    else:
        return None

    w, h = pil.size
    if w > max_w:
        scale = max_w / float(w)
        pil = pil.resize((max_w, max(1, int(h * scale))))
    return pil


def cover_thumbnail(path, max_w=240):
    """Return cached JPEG bytes for a book's cover thumbnail, or a clean placeholder JPEG."""
    from PIL import Image, ImageDraw

    p = Path(path)
    if not p.is_file():
        return None
    try:
        stamp = f"{p.stat().st_mtime_ns}_{p.stat().st_size}"
    except OSError:
        stamp = "0"
    cache_key = hashlib.sha256(f"{p.resolve()}:{stamp}:{max_w}".encode("utf-8", errors="ignore")).hexdigest()[:20]
    cache_file = _thumbs_dir() / f"{cache_key}.jpg"
    if cache_file.is_file():
        try:
            return cache_file.read_bytes()
        except OSError:
            pass

    pil = None
    try:
        pil = _render_cover_pil(p, max_w=max_w)
    except Exception as exc:
        _LOG.debug("bookshelf.cover_thumbnail %s: %s", p, exc)

    if pil is None:
        pil = Image.new("RGB", (max_w, int(max_w * 1.38)), (34, 38, 48))
        draw = ImageDraw.Draw(pil)
        draw.rectangle((14, 14, max_w - 14, int(max_w * 1.38) - 14), outline=(90, 105, 130), width=2)
        draw.text((24, 36), p.suffix.lstrip(".").upper() or "BOOK", fill=(210, 220, 235))
        draw.text((24, 64), p.stem[:22], fill=(160, 175, 195))

    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=82, optimize=True)
    raw = buf.getvalue()
    try:
        cache_file.write_bytes(raw)
    except OSError:
        pass
    return raw


def sibling_chapters(path):
    """Return natural-sorted previous and next readable books in the same folder."""
    p = Path(path)
    if not p.is_file():
        return {"prev": None, "next": None, "index": 0, "total": 0}
    parent = p.parent
    siblings = sorted(
        (
            e for e in parent.iterdir()
            if e.is_file() and not e.name.startswith(".") and e.suffix.lower() in BOOK_EXTS
        ),
        key=lambda e: _natural_key(e.name),
    )
    resolved = p.resolve()
    idx = -1
    for i, s in enumerate(siblings):
        if s.resolve() == resolved:
            idx = i
            break
    if idx < 0:
        return {"prev": None, "next": None, "index": 0, "total": len(siblings)}
    return {
        "prev": str(siblings[idx - 1].resolve()) if idx > 0 else None,
        "next": str(siblings[idx + 1].resolve()) if idx + 1 < len(siblings) else None,
        "index": idx + 1,
        "total": len(siblings),
    }
