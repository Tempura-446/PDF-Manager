# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Smart auto-rename and filing rules engine (offline, dry-run first, undoable).

Rules match PDFs by filename glob, page text / regex (including named capture
groups like ``(?P<vendor>...)``), page count, metadata, or detected page-1 date,
and render safe cross-platform target paths with collision avoidance and an
undo manifest.
"""

import fnmatch
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from pypdf import PdfWriter

from pdfmanager import appdata, core

_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DATE_PATTERNS = [
    # ISO: 2026-11-18 or 2026/11/18
    (re.compile(r"\b(20\d{2}|19\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b"), "ymd"),
    # Month Day, Year: Nov 18, 2026 / November 18 2026
    (re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?,?\s+(20\d{2}|19\d{2})\b", re.I), "mdy_name"),
    # Day Month Year: 18 Nov 2026 / 18 November 2026
    (re.compile(r"\b(0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?,?\s+(20\d{2}|19\d{2})\b", re.I), "dmy_name"),
    # DD.MM.YYYY (European dot format)
    (re.compile(r"\b(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.(20\d{2}|19\d{2})\b"), "dmy"),
    # MM/DD/YYYY (US slash format)
    (re.compile(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/(20\d{2}|19\d{2})\b"), "mdy"),
]

STARTER_RULES = [
    {
        "name": "Invoices & Receipts",
        "conditions": {
            "regex": r"(?i)(?:invoice|receipt).*?(?:no\.?|#|number)\s*[:#]?\s*(?P<invoice_no>[A-Z0-9-]+)",
        },
        "template": "{year}/Invoices/{date}_{invoice_no}.pdf",
    },
    {
        "name": "Bank Statements",
        "conditions": {
            "contains": ["statement"],
            "has_date": True,
        },
        "template": "{year}/Statements/{date}_{orig_name}.pdf",
    },
    {
        "name": "Dated Scans",
        "conditions": {
            "has_date": True,
        },
        "template": "{year}/{month}/{date}_{orig_name}.pdf",
    },
]


def extract_date(text):
    """Find the first plausible calendar date in ``text`` and return ``YYYY-MM-DD`` or ``''``."""
    if not text:
        return ""
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "mdy_name":
                mo = _MONTHS[m.group(1).lower().rstrip(".")]
                d, y = int(m.group(2)), int(m.group(3))
            elif kind == "dmy_name":
                d = int(m.group(1))
                mo = _MONTHS[m.group(2).lower().rstrip(".")]
                y = int(m.group(3))
            elif kind == "dmy":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:  # mdy
                mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dt = datetime(y, mo, d)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, KeyError):
            continue
    return ""


def sanitize_part(value, max_len=120):
    """Make a single filename or folder segment safe across Windows, macOS, and Linux."""
    text = str(value or "")
    text = _FORBIDDEN_CHARS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or text in (".", ".."):
        return ""
    if text.upper() in _RESERVED_NAMES:
        text = f"_{text}"
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .")
    return text


def render_template(template, variables, subfolder=""):
    """Render a relative target path (POSIX-style segments) from ``template`` and ``variables``."""
    raw = str(template or "{orig_name}.pdf").strip()
    if subfolder:
        raw = str(subfolder).strip("/\\") + "/" + raw.lstrip("/\\")

    def replace_var(match):
        key = match.group(1).strip()
        return sanitize_part(variables.get(key, ""), max_len=80)

    rendered = re.sub(r"\{([^{}]+)\}", replace_var, raw)
    parts = [p for p in re.split(r"[/\\]+", rendered) if p]
    safe_parts = []
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            stem = part[:-4] if part.lower().endswith(".pdf") else part
            clean_stem = sanitize_part(stem, max_len=120) or sanitize_part(variables.get("orig_name", "document")) or "document"
            # Collapse repeated separators left by empty optional variables
            clean_stem = re.sub(r"[_-]{2,}", "_", clean_stem).strip(" _-") or "document"
            safe_parts.append(clean_stem + ".pdf")
        else:
            clean_dir = sanitize_part(part, max_len=80)
            if clean_dir:
                safe_parts.append(clean_dir)
    if not safe_parts:
        safe_parts = [(sanitize_part(variables.get("orig_name", "document")) or "document") + ".pdf"]
    return Path(*safe_parts)


def extract_fields(path, password=None, ocr_if_scanned=False):
    """Inspect ``path`` and return extracted text, metadata, and built-in template variables."""
    src = Path(path)
    reader = core.open_pdf(src, password=password)
    pages = len(reader.pages)
    page1_text = ""
    sample_texts = []
    for idx in range(min(pages, 3)):
        txt = (reader.pages[idx].extract_text() or "").strip()
        if idx == 0:
            page1_text = txt
        if txt:
            sample_texts.append(txt)
    full_text = "\n".join(sample_texts)

    if ocr_if_scanned and not full_text.strip() and pages > 0:
        try:
            import tempfile
            from pdfmanager import ocr
            with tempfile.TemporaryDirectory(prefix="pdfm-file-ocr-") as tmp:
                tmp_out = Path(tmp) / "ocr.pdf"
                ocr.ocr_pdf(src, tmp_out, password=password)
                ocr_reader = core.open_pdf(tmp_out)
                page1_text = (ocr_reader.pages[0].extract_text() or "").strip() if ocr_reader.pages else ""
                full_text = "\n".join((p.extract_text() or "").strip() for p in ocr_reader.pages[:3]).strip()
        except Exception:
            pass

    meta = reader.metadata or {}
    title = str(meta.get("/Title") or "").strip()
    author = str(meta.get("/Author") or "").strip()
    subject = str(meta.get("/Subject") or "").strip()
    first_line = next((ln.strip() for ln in page1_text.splitlines() if ln.strip()), "")
    iso_date = extract_date(page1_text) or extract_date(full_text)
    year, month, day = (iso_date.split("-") if iso_date else ("", "", ""))

    return {
        "path": src,
        "filename": src.name,
        "pages": pages,
        "page1_text": page1_text,
        "text": full_text,
        "metadata": {"title": title, "author": author, "subject": subject},
        "variables": {
            "date": iso_date,
            "year": year,
            "month": month,
            "day": day,
            "first_line": first_line,
            "title": title,
            "author": author,
            "subject": subject,
            "pages": str(pages),
            "orig_name": src.stem,
        },
    }


def match_rule(info, rule):
    """Check whether ``info`` matches ``rule``. Returns ``(matched: bool, captures: dict, reason: str)``."""
    cond = rule.get("conditions") or {}
    captures = {}

    glob = cond.get("filename_glob")
    if glob and not fnmatch.fnmatch(info["filename"].lower(), str(glob).lower()):
        return False, {}, "filename_glob mismatch"

    min_pages = cond.get("min_pages")
    if min_pages is not None and info["pages"] < int(min_pages):
        return False, {}, "below min_pages"
    max_pages = cond.get("max_pages")
    if max_pages is not None and info["pages"] > int(max_pages):
        return False, {}, "above max_pages"

    if cond.get("has_date") is True and not info["variables"]["date"]:
        return False, {}, "no date found"
    if cond.get("has_date") is False and info["variables"]["date"]:
        return False, {}, "unexpected date found"

    contains = cond.get("contains")
    if contains:
        needles = [contains] if isinstance(contains, str) else list(contains)
        hay = info["text"].lower()
        for needle in needles:
            if str(needle).lower() not in hay:
                return False, {}, f"missing text {needle!r}"

    meta_cond = cond.get("metadata") or {}
    for k, expected in meta_cond.items():
        actual = info["metadata"].get(str(k).lower(), "").lower()
        if str(expected).lower() not in actual:
            return False, {}, f"metadata {k} mismatch"

    pattern = cond.get("regex")
    if pattern:
        m = re.search(str(pattern), info["text"], re.MULTILINE)
        if not m:
            return False, {}, "regex mismatch"
        for k, v in m.groupdict().items():
            if v is not None:
                captures[k] = v.strip()

    return True, captures, f"matched rule '{rule.get('name') or 'unnamed'}'"


def _resolve_collision(dst, reserved):
    """Avoid overwriting existing files or earlier targets in the same batch."""
    candidate = Path(dst)
    if not candidate.exists() and candidate.resolve() not in reserved:
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    idx = 2
    while True:
        alt = parent / f"{stem}-{idx}{suffix}"
        if not alt.exists() and alt.resolve() not in reserved:
            return alt
        idx += 1


def plan_filing(files, rules, *, base_dir=None, password=None):
    """Dry-run filing rules over ``files``. Returns a list of planned actions without touching disk."""
    plan = []
    reserved = set()
    any_ocr = any(bool(r.get("ocr_if_scanned")) for r in rules)

    for item in files:
        src = Path(item).resolve()
        info = extract_fields(src, password=password, ocr_if_scanned=any_ocr)
        matched_rule = None
        captures = {}
        reason = "no rule matched"
        for rule in rules:
            ok, caps, why = match_rule(info, rule)
            if ok:
                matched_rule = rule
                captures = caps
                reason = why
                break

        if not matched_rule:
            plan.append({
                "src": str(src),
                "dst": str(src),
                "rule": None,
                "matched": False,
                "reason": reason,
                "variables": info["variables"],
                "metadata": {},
            })
            continue

        merged_vars = {**info["variables"], **captures}
        rel = render_template(
            matched_rule.get("template") or "{orig_name}.pdf",
            merged_vars,
            subfolder=matched_rule.get("subfolder") or "",
        )
        root = Path(base_dir).resolve() if base_dir else src.parent
        raw_dst = (root / rel).resolve()
        if raw_dst == src:
            final_dst = src
        else:
            final_dst = _resolve_collision(raw_dst, reserved)
        reserved.add(final_dst.resolve())

        plan.append({
            "src": str(src),
            "dst": str(final_dst),
            "rule": matched_rule.get("name") or "unnamed",
            "matched": True,
            "reason": reason,
            "variables": merged_vars,
            "metadata": dict(matched_rule.get("metadata") or {}),
        })
    return plan


def _write_metadata(path, metadata, password=None):
    if not metadata:
        return
    reader = core.open_pdf(path, password=password)
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    existing = dict(reader.metadata or {})
    mapping = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords"}
    for k, v in metadata.items():
        pdf_key = mapping.get(str(k).lower())
        if pdf_key and v is not None:
            existing[pdf_key] = str(v)
    writer.add_metadata(existing)
    with open(path, "wb") as fh:
        writer.write(fh)


def default_undo_path():
    return appdata.data_dir() / "filing-undo.json"


def apply_filing(plan, *, undo_path=None, password=None):
    """Execute a filing ``plan``, recording an undo manifest so ``undo_filing()`` can roll back."""
    moves = []
    for entry in plan:
        if not entry.get("matched"):
            continue
        src = Path(entry["src"])
        dst = Path(entry["dst"])
        if not src.exists():
            continue
        if src.resolve() != dst.resolve():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moves.append({"src": str(src), "dst": str(dst)})
        if entry.get("metadata"):
            _write_metadata(dst, entry["metadata"], password=password)

    manifest_path = Path(undo_path) if undo_path else default_undo_path()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"moves": moves}, indent=2), encoding="utf-8")
    return {"moved": len(moves), "undo_path": str(manifest_path), "moves": moves}


def undo_filing(undo_path=None):
    """Reverse the last ``apply_filing()`` batch using its undo manifest."""
    manifest_path = Path(undo_path) if undo_path else default_undo_path()
    if not manifest_path.is_file():
        raise core.PdfManagerError("no filing undo log found")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    moves = data.get("moves") or []
    restored = 0
    for item in reversed(moves):
        src = Path(item["src"])
        dst = Path(item["dst"])
        if dst.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            restored += 1
            # Clean up empty parent directories created during filing (best-effort)
            parent = dst.parent
            try:
                if parent != src.parent and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
    manifest_path.unlink(missing_ok=True)
    return {"restored": restored}


def load_rules(path):
    """Load rules from a JSON (or YAML if PyYAML is installed) file."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yml", ".yaml"):
        try:
            import yaml
            data = yaml.safe_load(text)
        except ImportError as exc:
            raise core.PdfManagerError("PyYAML is required to read .yaml rule files; use .json instead") from exc
    else:
        data = json.loads(text)
    if isinstance(data, dict) and "rules" in data:
        data = data["rules"]
    if not isinstance(data, list):
        raise core.PdfManagerError("rules file must contain a list of rules")
    return data


def save_rules(path, rules):
    """Save a list of rules to ``path`` as formatted JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"rules": list(rules)}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def app_rules_path():
    """Default location of the user's saved filing rules in app data."""
    return appdata.data_dir() / "filing-rules.json"


def load_app_rules():
    """Return the user's saved filing rules, or a copy of ``STARTER_RULES`` if none saved yet."""
    path = app_rules_path()
    if not path.is_file():
        return [json.loads(json.dumps(r)) for r in STARTER_RULES]
    try:
        return load_rules(path)
    except (OSError, ValueError, core.PdfManagerError):
        return [json.loads(json.dumps(r)) for r in STARTER_RULES]


def save_app_rules(rules):
    """Persist the user's filing rules list to app data."""
    if not isinstance(rules, list):
        raise core.PdfManagerError("filing rules must be a list")
    return save_rules(app_rules_path(), rules)
