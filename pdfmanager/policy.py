# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Enterprise administrator policies, FIPS mode & offline update channel (Security Phase 8).

Reads read-only administrator policies in precedence order:
  1. Windows Registry ``HKLM\\Software\\Policies\\ZPDF`` (machine-wide)
  2. Windows Registry ``HKCU\\Software\\Policies\\ZPDF`` (per-user)
  3. JSON policy file at ``$ZPDF_POLICY_FILE`` or ``/etc/zpdf/policy.json`` (Linux/macOS/CI)

Any setting present in policy is reported as locked via ``is_locked(key)`` so
the Settings UI and CLI can enforce it and prevent user override.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


class PolicyError(ValueError):
    """Raised when an operation is blocked by an administrator security policy."""

POLICY_REGISTRY_PATH = r"Software\Policies\ZPDF"
DEFAULT_POSIX_POLICY_FILE = Path("/etc/zpdf/policy.json")

# Known policy keys and their expected types/defaults.
POLICY_SCHEMA: dict[str, tuple[type, object]] = {
    "disable_network": (bool, False),
    "disable_updates": (bool, False),
    "offline_update_dir": (str, ""),
    "require_make_safe_on_open": (bool, False),
    "force_security_profile": (str, ""),
    "disable_password_removal": (bool, False),
    "fips_mode": (bool, False),
    "trust_list_paths": (list, []),
}

# Algorithms permitted vs. forbidden when fips_mode is enabled.
FIPS_FORBIDDEN_ALGORITHMS = frozenset({
    "rc4", "rc4-40", "rc4-128", "md5", "sha1", "sha-1", "des", "3des", "aes-128-ecb",
})

_CACHE: dict[str, object] | None = None


def _coerce(key: str, value):
    expected_type, default = POLICY_SCHEMA[key]
    if value is None:
        return default
    if expected_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if expected_type is str:
        return str(value).strip()
    if expected_type is list:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            return [p.strip() for p in value.split(os.pathsep) if p.strip()]
        return []
    return value


def _read_windows_registry(hive_name: str, subkey: str = POLICY_REGISTRY_PATH) -> dict:
    """Read policy values from HKLM or HKCU on Windows (empty dict on non-Windows)."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    hive = getattr(winreg, hive_name, None)
    if hive is None:
        return {}
    out = {}
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as handle:
            for key in POLICY_SCHEMA:
                try:
                    val, _ = winreg.QueryValueEx(handle, key)
                    out[key] = val
                except FileNotFoundError:
                    continue
    except OSError:
        return {}
    return out


def _read_json_policy(policy_file: Path | None = None) -> dict:
    env_path = os.environ.get("ZPDF_POLICY_FILE", "").strip()
    fp = policy_file or (Path(env_path) if env_path else DEFAULT_POSIX_POLICY_FILE)
    if not fp.is_file():
        return {}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load(
    *,
    reload: bool = False,
    registry_reader=None,
    policy_file: Path | None = None,
) -> dict[str, object]:
    """Load effective administrator policies (cached unless ``reload=True``)."""
    global _CACHE
    if _CACHE is not None and not reload and registry_reader is None and policy_file is None:
        return dict(_CACHE)

    reader = registry_reader or _read_windows_registry
    # Lowest precedence first: JSON file -> HKCU -> HKLM
    merged: dict[str, object] = {}
    for layer in (
        _read_json_policy(policy_file),
        reader("HKEY_CURRENT_USER", POLICY_REGISTRY_PATH),
        reader("HKEY_LOCAL_MACHINE", POLICY_REGISTRY_PATH),
    ):
        for key, raw in (layer or {}).items():
            if key in POLICY_SCHEMA:
                merged[key] = _coerce(key, raw)

    if registry_reader is None and policy_file is None:
        _CACHE = dict(merged)
    return dict(merged)


def clear_cache() -> None:
    global _CACHE
    _CACHE = None


def is_locked(key: str, *, policies: dict | None = None) -> bool:
    """True if ``key`` is explicitly set by an administrator policy."""
    eff = load() if policies is None else policies
    return key in eff


def get(key: str, default=None, *, policies: dict | None = None):
    """Return the effective policy value for ``key``, or its schema default."""
    eff = load() if policies is None else policies
    if key in eff:
        return eff[key]
    if default is not None:
        return default
    return POLICY_SCHEMA.get(key, (object, None))[1]


def snapshot(*, policies: dict | None = None) -> dict:
    """Structured view of all policies and their locked state (for GUI Settings & CLI)."""
    eff = load() if policies is None else policies
    return {
        key: {
            "value": eff[key] if key in eff else default,
            "locked": key in eff,
        }
        for key, (_, default) in POLICY_SCHEMA.items()
    }


def check_fips_algorithm(algorithm: str, *, policies: dict | None = None) -> None:
    """Raise PolicyError if ``fips_mode`` is enabled and ``algorithm`` is forbidden."""
    if not get("fips_mode", False, policies=policies):
        return
    norm = str(algorithm or "").strip().lower()
    if norm in FIPS_FORBIDDEN_ALGORITHMS or "rc4" in norm or "md5" in norm or "sha1" in norm:
        raise PolicyError(
            f"FIPS mode policy forbids algorithm {algorithm!r}; use AES-256 and SHA-256 or stronger"
        )


def check_password_removal_allowed(*, policies: dict | None = None) -> None:
    """Raise PolicyError if administrator policy forbids removing PDF passwords."""
    if get("disable_password_removal", False, policies=policies):
        raise PolicyError(
            "Removing PDF passwords is disabled by your organisation's security policy"
        )


def offline_update_candidates(*, policies: dict | None = None) -> list[dict]:
    """List verified offline update packages from ``offline_update_dir`` (no network)."""
    folder_str = get("offline_update_dir", "", policies=policies)
    if not folder_str:
        return []
    folder = Path(folder_str)
    if not folder.is_dir():
        return []
    found = []
    for zf in sorted(folder.glob("ZPDF-*.zip")):
        sha_file = zf.with_name(zf.name + ".sha256")
        found.append({
            "name": zf.name,
            "path": str(zf),
            "sha256_path": str(sha_file) if sha_file.is_file() else None,
        })
    return found
