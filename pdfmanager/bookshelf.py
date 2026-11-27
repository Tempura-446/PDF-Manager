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
import threading
import zipfile
from pathlib import Path

from pdfmanager import appdata, cbz, core

BOOK_EXTS = {".pdf", ".cbz", ".cbr", ".epub"}
_LOG = logging.getLogger("zpdf")
_CACHE_LOCK = threading.Lock()
_SCAN_KEY = None
_SCAN_ITEMS = []
_ID_INDEX = {}


def _invalidate_cache():
    global _SCAN_KEY, _SCAN_ITEMS, _ID_INDEX
    with _CACHE_LOCK:
        _SCAN_KEY = None
        _SCAN_ITEMS = []
        _ID_INDEX = {}


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
    _invalidate_cache()


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
    import os

    raw = str(folder_path or "").strip()
    norm = os.path.normpath(os.path.expanduser(raw)) if raw else ""
    folders = [f for f in list_folders() if f != raw and f != norm]
    _save_folders(folders)
    return folders


def _folders_fingerprint(folders):
    stamps = []
    for folder in folders:
        try:
            stamps.append((folder, Path(folder).stat().st_mtime_ns))
        except OSError:
            stamps.append((folder, 0))
    return (str(_config_path()), tuple(stamps))


def _all_items_cached(folders):
    global _SCAN_KEY, _SCAN_ITEMS, _ID_INDEX
    fp = _folders_fingerprint(folders)
    with _CACHE_LOCK:
        if _SCAN_KEY == fp:
            return _SCAN_ITEMS

    items = []
    seen = set()
    id_map = {}
    for folder in folders:
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
            bid = _book_id(resolved)
            id_map[bid] = Path(resolved)
            items.append({
                "id": bid,
                "path": resolved,
                "name": entry.name,
                "stem": entry.stem,
                "ext": ext.lstrip(".").upper(),
                "folder": root.name or str(root),
                "size_bytes": size_bytes,
                "mtime": mtime,
            })

    with _CACHE_LOCK:
        _SCAN_KEY = fp
        _SCAN_ITEMS = items
        _ID_INDEX = id_map
    return items


def scan_library(query="", *, max_items=400):
    """Scan all configured Bookshelf folders and return natural-sorted book items
    enriched with per-book reading progress and a Continue Reading recent list."""
    folders = list_folders()
    all_items = _all_items_cached(folders)
    q = str(query or "").strip().lower()
    if q:
        filtered = [it for it in all_items if q in it["name"].lower() or q in it["folder"].lower()]
    else:
        filtered = all_items
    prog_map = _load_progress()
    enriched = []
    for it in filtered[:max_items]:
        copy = dict(it)
        p = prog_map.get(it["path"])
        if p:
            copy["progress"] = dict(p)
        enriched.append(copy)
    return {
        "folders": folders,
        "items": enriched,
        "recent": list_recent_progress(limit=8, _prog_map=prog_map),
    }


def find_by_id(book_id):
    """Resolve a 16-char bookshelf item id back to its Path on disk in O(1)."""
    with _CACHE_LOCK:
        hit = _ID_INDEX.get(book_id)
    if hit is not None and hit.is_file():
        return hit
    _all_items_cached(list_folders())
    with _CACHE_LOCK:
        hit = _ID_INDEX.get(book_id)
    if hit is not None and hit.is_file():
        return hit
    # Fallback: check recently read books (which may live outside configured folders)
    for raw_path in _load_progress():
        if _book_id(raw_path) == book_id:
            p = Path(raw_path)
            if p.is_file():
                return p
    return None


def _render_cover_pil(path, max_w=240):
    from PIL import Image

    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium

        with core.PDFIUM_LOCK:
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
        from PIL import ImageOps

        with zipfile.ZipFile(p, "r") as zf:
            imgs = [
                inf for inf in zf.infolist()
                if not inf.is_dir()
                and inf.file_size <= 32 * 1024 * 1024
                and not Path(inf.filename).name.startswith(".")
                and "__MACOSX" not in inf.filename.split("/")
                and Path(inf.filename).suffix.lower() in cbz.IMAGE_EXTS
            ]
            if not imgs:
                return None
            imgs.sort(key=lambda inf: _natural_key(inf.filename))
            with Image.open(io.BytesIO(zf.read(imgs[0]))) as im:
                pil = ImageOps.exif_transpose(im).convert("RGB")
    elif ext == ".epub" and zipfile.is_zipfile(p):
        from PIL import ImageOps

        with zipfile.ZipFile(p, "r") as zf:
            imgs = [
                inf for inf in zf.infolist()
                if not inf.is_dir()
                and inf.file_size <= 32 * 1024 * 1024
                and not Path(inf.filename).name.startswith(".")
                and "__MACOSX" not in inf.filename.split("/")
                and Path(inf.filename).suffix.lower() in cbz.IMAGE_EXTS
            ]
            if not imgs:
                return None
            # Prefer a file with "cover" in its name, otherwise first image
            imgs.sort(key=lambda inf: (0 if "cover" in inf.filename.lower() else 1, _natural_key(inf.filename)))
            with Image.open(io.BytesIO(zf.read(imgs[0]))) as im:
                pil = ImageOps.exif_transpose(im).convert("RGB")
    else:
        return None

    w, h = pil.size
    if w > max_w:
        scale = max_w / float(w)
        pil = pil.resize((max_w, max(1, int(h * scale))))
    return pil


def cover_thumbnail(path, max_w=240):
    """Return cached JPEG bytes for a book's cover thumbnail, or a clean placeholder JPEG."""
    import os
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
        part = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.part")
        part.write_bytes(raw)
        os.replace(part, cache_file)
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


_PROGRESS_LOCK = threading.Lock()
_MAX_PROGRESS_ENTRIES = 500


def _progress_path():
    return appdata.data_dir() / "reading-progress.json"


def _load_progress():
    p = _progress_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("bookshelf._load_progress: %s", exc)
        return {}


def _write_progress(data):
    import os

    p = _progress_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    part = p.with_name(f"{p.name}.{os.getpid()}.part")
    part.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(part, p)


def save_progress(path, *, page=1, total=0, zoom="", rtl=False, spread=False):
    """Persist per-book reading position and view preferences."""
    import time

    resolved = str(Path(path).resolve())
    page_n = max(1, int(page or 1))
    total_n = max(0, int(total or 0))
    if total_n > 0:
        page_n = min(page_n, total_n)
        percent = min(100, max(0, round((page_n / total_n) * 100)))
    else:
        percent = 0
    entry = {
        "page": page_n,
        "total": total_n,
        "percent": percent,
        "zoom": str(zoom or "")[:32],
        "rtl": bool(rtl),
        "spread": bool(spread),
        "updated_at": round(time.time(), 2),
    }
    with _PROGRESS_LOCK:
        data = _load_progress()
        data[resolved] = entry
        if len(data) > _MAX_PROGRESS_ENTRIES:
            ordered = sorted(
                data.items(),
                key=lambda kv: float((kv[1] or {}).get("updated_at") or 0),
                reverse=True,
            )
            data = dict(ordered[:_MAX_PROGRESS_ENTRIES])
        try:
            _write_progress(data)
        except OSError as exc:
            _LOG.debug("bookshelf.save_progress: %s", exc)
    return entry


def get_progress(path):
    """Return saved reading progress dict for ``path``, or None."""
    if not path:
        return None
    resolved = str(Path(path).resolve())
    with _PROGRESS_LOCK:
        entry = _load_progress().get(resolved)
    return dict(entry) if isinstance(entry, dict) else None


def list_recent_progress(limit=12, *, _prog_map=None):
    """Return up to ``limit`` recently read books that still exist on disk."""
    data = _prog_map if _prog_map is not None else _load_progress()
    ranked = sorted(
        data.items(),
        key=lambda kv: float((kv[1] or {}).get("updated_at") or 0),
        reverse=True,
    )
    out = []
    for raw_path, prog in ranked:
        if len(out) >= limit:
            break
        if not isinstance(prog, dict):
            continue
        p = Path(raw_path)
        if not p.is_file() or p.suffix.lower() not in BOOK_EXTS:
            continue
        out.append({
            "id": _book_id(raw_path),
            "path": raw_path,
            "name": p.name,
            "stem": p.stem,
            "ext": p.suffix.lstrip(".").upper(),
            "folder": p.parent.name or str(p.parent),
            "progress": dict(prog),
        })
    return out


def _make_snippet(text, pos, length, radius=64):
    clean = " ".join(text.split())
    if not clean:
        return ("", "", "")
    # Re-locate inside whitespace-normalized string for clean UI rendering
    idx = clean.lower().find(text[pos:pos + length].lower())
    if idx < 0:
        idx = min(max(0, pos), max(0, len(clean) - 1))
    start = max(0, idx - radius)
    end = min(len(clean), idx + length + radius)
    before = ("…" if start > 0 else "") + clean[start:idx]
    match = clean[idx:idx + length]
    after = clean[idx + length:end] + ("…" if end < len(clean) else "")
    return (before, match, after)


def _search_pdf(path, query_lower, max_hits=3):
    from pypdf import PdfReader

    hits = []
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    return hits
            except Exception as exc:  # noqa: BLE001
                _LOG.debug("bookshelf._search_pdf decrypt %s: %s", path, exc)
                return hits
        for page_idx, page in enumerate(reader.pages, 1):
            try:
                txt = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                _LOG.debug("bookshelf._search_pdf page %s #%d: %s", path, page_idx, exc)
                continue
            pos = txt.lower().find(query_lower)
            if pos >= 0:
                before, match, after = _make_snippet(txt, pos, len(query_lower))
                hits.append({
                    "page": page_idx,
                    "before": before,
                    "match": match,
                    "after": after,
                })
                if len(hits) >= max_hits:
                    break
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("bookshelf._search_pdf %s: %s", path, exc)
    return hits


def _search_epub(path, query_lower, max_hits=3):
    from pdfmanager import epub

    hits = []
    if not zipfile.is_zipfile(path):
        return hits
    try:
        with zipfile.ZipFile(path, "r") as zf:
            members = sorted(
                (
                    inf for inf in zf.infolist()
                    if not inf.is_dir()
                    and inf.file_size <= 8 * 1024 * 1024
                    and Path(inf.filename).suffix.lower() in (".xhtml", ".html", ".htm")
                ),
                key=lambda inf: _natural_sort_key_epub(inf.filename),
            )
            for chap_idx, inf in enumerate(members, 1):
                try:
                    raw = zf.read(inf).decode("utf-8", errors="ignore")
                    parser = epub._ChapterParser()
                    parser.feed(raw)
                    parser.close()
                    txt = " ".join(t for tag, t in parser.blocks if tag != "img" and t)
                except Exception as exc:  # noqa: BLE001
                    _LOG.debug("bookshelf._search_epub %s %s: %s", path, inf.filename, exc)
                    continue
                pos = txt.lower().find(query_lower)
                if pos >= 0:
                    before, match, after = _make_snippet(txt, pos, len(query_lower))
                    hits.append({
                        "page": chap_idx,
                        "before": before,
                        "match": match,
                        "after": after,
                    })
                    if len(hits) >= max_hits:
                        break
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("bookshelf._search_epub %s: %s", path, exc)
    return hits


def _natural_sort_key_epub(name):
    return _natural_key(name)


def search_library(query, *, limit=50, max_per_book=3):
    """Offline full-text search across all PDFs and EPUBs in configured Bookshelf folders
    backed by a persistent incremental SQLite FTS5 index."""
    from pdfmanager import library_index

    all_items = _all_items_cached(list_folders())
    res = library_index.search_fts(query, all_items, limit=limit, max_per_book=max_per_book)
    for hit in res.get("results") or ():
        hit.pop("excerpt", None)
    return res


def ask_library(query, *, top_k=6, model=None):
    """Answer a question across all Bookshelf PDFs & EPUBs with local-LLM citations."""
    from pdfmanager import library_index

    all_items = _all_items_cached(list_folders())
    return library_index.ask_library(query, all_items, top_k=top_k, model=model)


def index_status():
    """Return SQLite FTS5 library index statistics."""
    from pdfmanager import library_index

    return library_index.index_status()


