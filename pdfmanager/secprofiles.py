# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Named security profiles and dynamic print/export watermarks (Security Phase 7).

A Security Profile bundles repeatable protection steps (Make safe, remove
hidden data / metadata, dynamic or static watermark, and AES-256 or
certificate encryption) under a single name so teams can apply, export, and
import consistent document-protection rules across CLI, GUI, and Watch folders.
"""

from __future__ import annotations

import getpass
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from pdfmanager import archive, auditlog, core, filetools, risks
from pdfmanager.appdata import data_dir

BUILTIN_PROFILES: dict[str, dict] = {
    "Confidential": {
        "name": "Confidential",
        "description": "Make safe, strip metadata & attachments, stamp CONFIDENTIAL watermark, and restrict printing/copying.",
        "make_safe": True,
        "remove_hidden": True,
        "watermark_text": "CONFIDENTIAL",
        "watermark_opacity": 0.16,
        "watermark_angle": 45,
        "permissions": {"printing": "none", "changes": "none", "copy": False},
        "builtin": True,
    },
    "Internal": {
        "name": "Internal",
        "description": "Make safe, strip metadata, and stamp dynamic internal copy watermark.",
        "make_safe": True,
        "remove_hidden": True,
        "watermark_text": "INTERNAL — {user} — {date}",
        "watermark_opacity": 0.12,
        "watermark_angle": 45,
        "permissions": {"printing": "high", "changes": "none", "copy": True},
        "builtin": True,
    },
    "Public Share": {
        "name": "Public Share",
        "description": "Make safe and remove hidden metadata, comments, scripts, and attachments for external sharing.",
        "make_safe": True,
        "remove_hidden": True,
        "remove_comments": True,
        "watermark_text": "",
        "permissions": None,
        "builtin": True,
    },
}


def profiles_file(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else (data_dir() / "security_profiles.json")


def _load_custom(path: str | Path | None = None) -> dict[str, dict]:
    fp = profiles_file(path)
    if not fp.exists():
        return {}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_custom(profiles: dict[str, dict], path: str | Path | None = None) -> None:
    from pdfmanager.appdata import write_private

    fp = profiles_file(path)
    write_private(fp, json.dumps(profiles, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def list_profiles(store_path: str | Path | None = None) -> list[dict]:
    """All available profiles (built-ins merged with user-saved overrides/customs)."""
    merged = {k: dict(v) for k, v in BUILTIN_PROFILES.items()}
    for name, prof in _load_custom(store_path).items():
        merged[name] = dict(prof, name=name, builtin=False)
    return [merged[k] for k in sorted(merged)]


def get_profile(name: str, store_path: str | Path | None = None) -> dict:
    """Return profile dict by name (case-insensitive fallback) or raise PdfManagerError."""
    all_profs = {p["name"]: p for p in list_profiles(store_path)}
    if name in all_profs:
        return dict(all_profs[name])
    low = name.strip().lower()
    for k, v in all_profs.items():
        if k.lower() == low:
            return dict(v)
    raise core.PdfManagerError(f"unknown security profile {name!r}")


def save_profile(name: str, config: dict, store_path: str | Path | None = None) -> dict:
    """Create or update a named custom security profile."""
    clean_name = name.strip()
    if not clean_name:
        raise core.PdfManagerError("security profile name can't be empty")
    custom = _load_custom(store_path)
    entry = dict(config)
    entry["name"] = clean_name
    entry["builtin"] = False
    custom[clean_name] = entry
    _save_custom(custom, store_path)
    return dict(entry)


def delete_profile(name: str, store_path: str | Path | None = None) -> None:
    custom = _load_custom(store_path)
    if name not in custom:
        raise core.PdfManagerError(f"no custom security profile called {name!r}")
    del custom[name]
    _save_custom(custom, store_path)


def export_profile(name: str, output: str | Path, store_path: str | Path | None = None) -> Path:
    """Export a single profile as a shareable JSON file (never exports stored passwords)."""
    prof = get_profile(name, store_path)
    prof.pop("user_password", None)
    prof.pop("owner_password", None)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"zpdf_security_profile": 1, "profile": prof}
    out.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def import_profile(src: str | Path, store_path: str | Path | None = None) -> dict:
    """Import a profile from a JSON file and save it locally."""
    fp = Path(src)
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise core.PdfManagerError(f"could not read security profile {fp}: {exc}") from None
    prof = data.get("profile") if isinstance(data, dict) and "profile" in data else data
    if not isinstance(prof, dict) or not prof.get("name"):
        raise core.PdfManagerError(f"{fp} is not a valid ZPDF security profile")
    return save_profile(str(prof["name"]), prof, store_path)


def expand_watermark_template(
    template: str,
    *,
    user: str | None = None,
    now: datetime | None = None,
    filename: str | None = None,
) -> str:
    """Expand ``{user}``, ``{date}``, ``{time}``, ``{datetime}``, and ``{file}`` placeholders."""
    ts = now or datetime.now()
    if user is None:
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001
            user = "unknown"
    values = {
        "user": user,
        "date": ts.strftime("%Y-%m-%d"),
        "time": ts.strftime("%H:%M"),
        "datetime": ts.strftime("%Y-%m-%d %H:%M"),
        "file": Path(filename).name if filename else "",
    }
    text = str(template)
    for key, val in values.items():
        text = text.replace("{" + key + "}", val)
    return text


def dynamic_print_watermark(
    path: str | Path,
    output: str | Path,
    *,
    template: str = "Printed copy — {user} — {datetime}",
    opacity: float = 0.18,
    angle: float = 45,
    user: str | None = None,
    now: datetime | None = None,
    password: str | None = None,
) -> Path:
    """Stamp a dynamic traceable print/export watermark onto every page."""
    stamped_text = expand_watermark_template(template, user=user, now=now, filename=str(path))
    result = core.watermark(
        path, output, text=stamped_text, opacity=opacity, angle=angle, password=password,
    )
    auditlog.record(
        "print_watermark",
        target=path,
        details={"template": template, "stamped": stamped_text},
    )
    return Path(result)


def apply_profile(
    path: str | Path,
    output: str | Path,
    profile: str | dict,
    *,
    password: str | None = None,
    user_password: str | None = None,
    owner_password: str | None = None,
    store_path: str | Path | None = None,
    audit_path: str | Path | None = None,
) -> dict:
    """Apply a named or dict Security Profile to ``path`` and write ``output``.

    Returns a summary dict ``{"profile": name, "output": str(output), "steps": [...]}``.
    """
    prof = get_profile(profile, store_path) if isinstance(profile, str) else dict(profile)
    prof_name = prof.get("name", "Custom")
    applied_steps = []

    with tempfile.TemporaryDirectory(prefix="zpdf-secprof-") as tmp:
        current = Path(tmp) / "step0.pdf"
        if core.open_pdf(path, password).is_encrypted:
            core.decrypt(path, current, password or "", _internal=True)
        else:
            shutil.copyfile(path, current)

        step_idx = 0

        if prof.get("make_safe"):
            step_idx += 1
            nxt = Path(tmp) / f"step{step_idx}.pdf"
            risks.make_safe(current, nxt)
            current = nxt
            applied_steps.append("make_safe")

        if prof.get("remove_hidden"):
            step_idx += 1
            nxt = Path(tmp) / f"step{step_idx}.pdf"
            archive.sanitize(
                current,
                nxt,
                metadata=True,
                javascript=True,
                attachments=True,
                comments=bool(prof.get("remove_comments", False)),
            )
            current = nxt
            applied_steps.append("remove_hidden")

        wm_template = (prof.get("watermark_text") or "").strip()
        if wm_template:
            step_idx += 1
            nxt = Path(tmp) / f"step{step_idx}.pdf"
            wm_text = expand_watermark_template(wm_template, filename=str(path))
            core.watermark(
                current,
                nxt,
                text=wm_text,
                opacity=float(prof.get("watermark_opacity", 0.15)),
                angle=float(prof.get("watermark_angle", 45)),
            )
            current = nxt
            applied_steps.append("watermark")

        eff_user_pw = user_password if user_password is not None else prof.get("user_password")
        eff_owner_pw = owner_password if owner_password is not None else prof.get("owner_password")
        perm_cfg = prof.get("permissions")
        if eff_user_pw or eff_owner_pw:
            import secrets

            step_idx += 1
            nxt = Path(tmp) / f"step{step_idx}.pdf"
            perms = None
            if isinstance(perm_cfg, dict):
                perms = filetools.permissions(
                    printing=perm_cfg.get("printing", "high"),
                    changes=perm_cfg.get("changes", "any"),
                    copy=bool(perm_cfg.get("copy", True)),
                )
            final_owner = eff_owner_pw
            if not final_owner or final_owner == (eff_user_pw or ""):
                # Restricted permissions require an owner password distinct from user_password.
                final_owner = secrets.token_urlsafe(18) if (perms is not None and filetools.restricts(perms)) else eff_user_pw
            core.encrypt(
                current,
                nxt,
                user_password=eff_user_pw or "",
                owner_password=final_owner,
                permissions=perms,
            )
            current = nxt
            applied_steps.append("encrypt")

        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current), str(out))

    auditlog.record(
        "profile_applied",
        target=path,
        details={"profile": prof_name, "steps": applied_steps},
        log_path=audit_path,
    )
    return {"profile": prof_name, "output": str(out), "steps": applied_steps}
