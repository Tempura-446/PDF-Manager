# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Named workspace sessions (open tabs, split-view layout, and reading positions).

Stored privately under ``appdata.data_dir() / "sessions.json"`` when privacy
allows remembering files. File paths are never accepted directly from the
browser; routes resolve ``fileId`` values through ``OpenFiles``.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from pdfmanager import appdata, privacy

MAX_SESSIONS = 30
MAX_TABS_PER_SESSION = 32


def _sessions_path() -> Path:
    return appdata.data_dir() / "sessions.json"


def _load_raw() -> list[dict]:
    if not privacy.remembering():
        return []
    path = _sessions_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (OSError, ValueError):
        return []
    return []


def _save_raw(items: list[dict]) -> None:
    if not privacy.remembering():
        return
    appdata.write_private(_sessions_path(), json.dumps(items[:MAX_SESSIONS], indent=2))


def list_sessions() -> list[dict]:
    """Return saved workspace sessions sorted newest first."""
    items = _load_raw()
    items.sort(key=lambda s: float(s.get("saved_at") or 0), reverse=True)
    return items


def save_session(
    name: str,
    tabs: list[dict],
    active_index: int = 0,
    split: dict | None = None,
) -> dict:
    """Save or update a named workspace session.

    If a session with the same case-insensitive name exists, it is updated in
    place (preserving its ``id``).
    """
    clean_name = (name or "").strip()[:80]
    if not clean_name:
        raise ValueError("Session name cannot be empty")
    clean_tabs: list[dict] = []
    for raw in (tabs or [])[:MAX_TABS_PER_SESSION]:
        if not isinstance(raw, dict):
            continue
        p = str(raw.get("path") or "").strip()
        if not p:
            continue
        page = max(1, int(raw.get("page") or 1))
        rot = int(raw.get("rotation") or 0) % 360
        scale = raw.get("scale")
        if not isinstance(scale, (int, float, str)):
            scale = "auto"
        clean_tabs.append({
            "path": p,
            "name": str(raw.get("name") or Path(p).name)[:180],
            "page": page,
            "scale": scale,
            "rotation": rot,
        })
    if not clean_tabs:
        raise ValueError("At least one saved file tab is required to save a workspace session")

    idx = max(0, min(int(active_index or 0), len(clean_tabs) - 1))
    clean_split = {"open": False}
    if isinstance(split, dict) and split.get("open"):
        ratio = float(split.get("ratio") or 0.5)
        ratio = max(0.2, min(0.8, round(ratio, 3)))
        clean_split = {
            "open": True,
            "source_index": split.get("source_index"),
            "source_special": split.get("source_special") if split.get("source_special") in ("__same__", "__translate__") else None,
            "ratio": ratio,
            "sync": bool(split.get("sync", True)),
            "offset": int(split.get("offset") or 0),
        }

    items = _load_raw()
    existing_id = None
    remaining = []
    for item in items:
        if str(item.get("name") or "").strip().lower() == clean_name.lower():
            existing_id = item.get("id")
        else:
            remaining.append(item)

    entry = {
        "id": existing_id or uuid.uuid4().hex[:12],
        "name": clean_name,
        "saved_at": int(time.time()),
        "active_index": idx,
        "tabs": clean_tabs,
        "split": clean_split,
    }
    remaining.insert(0, entry)
    _save_raw(remaining)
    return entry


def delete_session(session_id: str) -> bool:
    """Delete a saved session by ID. Returns True if found and removed."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    items = _load_raw()
    kept = [item for item in items if str(item.get("id") or "") != sid]
    if len(kept) == len(items):
        return False
    _save_raw(kept)
    return True
