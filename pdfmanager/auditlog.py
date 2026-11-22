# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Local, append-only, tamper-evident security audit log (Security Phase 7).

Each security-relevant action (encrypt, decrypt, sign, certify, redact,
make_safe, profile_applied, etc.) is appended as a single JSON line to
``<data_dir>/audit.jsonl``. Every entry includes a SHA-256 hash of its
canonical payload chained to the previous entry's hash, so any edited,
deleted, inserted, or reordered line is detected by ``verify_chain()``.
"""

from __future__ import annotations

import csv
import getpass
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from pdfmanager import applog
from pdfmanager.appdata import data_dir

GENESIS_HASH = "0" * 64
_LOCK = threading.Lock()


def audit_file(path: str | Path | None = None) -> Path:
    """Path to the local audit log file."""
    return Path(path) if path is not None else (data_dir() / "audit.jsonl")


def _canonical_payload(entry: dict) -> bytes:
    """Deterministic UTF-8 JSON bytes for hashing (excluding entry_hash)."""
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _compute_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical_payload(entry)).hexdigest()


def _last_hash(log_path: Path) -> str:
    if not log_path.exists():
        return GENESIS_HASH
    last_line = ""
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                last_line = stripped
    if not last_line:
        return GENESIS_HASH
    try:
        data = json.loads(last_line)
        return str(data.get("entry_hash") or GENESIS_HASH)
    except json.JSONDecodeError:
        return GENESIS_HASH


def _safe_actor(actor: str | None) -> str:
    if actor:
        return str(actor)
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unknown"


def _clean_details(details: dict | None) -> dict:
    if not details:
        return {}
    cleaned = {}
    for k, v in details.items():
        if isinstance(v, str):
            cleaned[str(k)] = applog.clean_message(v)
        elif isinstance(v, (int, float, bool)) or v is None:
            cleaned[str(k)] = v
        elif isinstance(v, (list, tuple)):
            cleaned[str(k)] = [applog.clean_message(x) if isinstance(x, str) else x for x in v]
        else:
            cleaned[str(k)] = applog.clean_message(str(v))
    return cleaned


def record(
    action: str,
    *,
    target: str | Path | None = None,
    actor: str | None = None,
    details: dict | None = None,
    status: str = "ok",
    log_path: str | Path | None = None,
) -> dict:
    """Append a hash-chained security audit entry and return the written dict."""
    path = audit_file(log_path)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = _last_hash(path)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": str(action),
            "status": str(status),
            "actor": _safe_actor(actor),
            "target": Path(target).name if target else None,
            "details": _clean_details(details),
            "prev_hash": prev,
        }
        entry["entry_hash"] = _compute_hash(entry)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
    return entry


def read_entries(
    *,
    action: str | None = None,
    limit: int | None = None,
    log_path: str | Path | None = None,
) -> list[dict]:
    """Read audit log entries, optionally filtered by action or limited to last N."""
    path = audit_file(log_path)
    if not path.exists():
        return []
    entries = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if action and item.get("action") != action:
                continue
            entries.append(item)
    if limit is not None and limit > 0:
        return entries[-limit:]
    return entries


def verify_chain(log_path: str | Path | None = None) -> dict:
    """Verify hash-chain integrity across every line of the audit log.

    Returns ``{"valid": bool, "count": int, "errors": [{"line": int, "reason": str}, ...]}``.
    """
    path = audit_file(log_path)
    if not path.exists():
        return {"valid": True, "count": 0, "errors": []}

    expected_prev = GENESIS_HASH
    count = 0
    errors = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, 1):
            stripped = raw.strip()
            if not stripped:
                continue
            count += 1
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as exc:
                errors.append({"line": line_no, "reason": f"malformed JSON ({exc})"})
                continue
            if not isinstance(entry, dict):
                errors.append({"line": line_no, "reason": "entry is not a JSON object"})
                continue
            if entry.get("prev_hash") != expected_prev:
                errors.append({
                    "line": line_no,
                    "reason": "broken chain (prev_hash does not match previous entry)",
                })
            computed = _compute_hash(entry)
            if entry.get("entry_hash") != computed:
                errors.append({
                    "line": line_no,
                    "reason": "tampered entry (entry_hash mismatch)",
                })
            expected_prev = str(entry.get("entry_hash") or "")

    return {"valid": len(errors) == 0, "count": count, "errors": errors}


def export_entries(
    output: str | Path,
    *,
    fmt: str = "json",
    action: str | None = None,
    log_path: str | Path | None = None,
) -> Path:
    """Export audit log entries to JSON or CSV."""
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    entries = read_entries(action=action, log_path=log_path)
    fmt = fmt.lower()
    if fmt == "json":
        out.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif fmt == "csv":
        fields = ["timestamp", "action", "status", "actor", "target", "details", "prev_hash", "entry_hash"]
        with out.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for entry in entries:
                row = dict(entry)
                row["details"] = json.dumps(row.get("details") or {}, sort_keys=True, ensure_ascii=False)
                writer.writerow({k: row.get(k, "") for k in fields})
    else:
        raise ValueError(f"unsupported audit export format {fmt!r} (use 'json' or 'csv')")
    return out
