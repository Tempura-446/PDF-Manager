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
    """Raise PolicyError if ``fips_mode`` is enabled and ``algorithm`` is forbidden.

    Note: This enforces ZPDF's algorithm-policy filter (rejecting RC4, MD5,
    SHA-1, DES, 3DES, and AES-128-ECB). It does **not** provide or claim
    FIPS 140-2/140-3 validated cryptographic module certification.
    """
    if not get("fips_mode", False, policies=policies):
        return
    norm = str(algorithm or "").strip().lower()
    if norm in FIPS_FORBIDDEN_ALGORITHMS or "rc4" in norm or "md5" in norm or "sha1" in norm or "sha-1" in norm:
        raise PolicyError(
            f"FIPS mode policy forbids algorithm {algorithm!r}; use AES-256 and SHA-256 or stronger"
        )


def check_password_removal_allowed(*, policies: dict | None = None) -> None:
    """Raise PolicyError if administrator policy forbids removing PDF passwords."""
    if get("disable_password_removal", False, policies=policies):
        raise PolicyError(
            "Removing PDF passwords is disabled by your organisation's security policy"
        )


def check_network_allowed(operation: str = "network access", *, policies: dict | None = None) -> None:
    """Raise PolicyError if administrator policy disables outbound network access."""
    if get("disable_network", False, policies=policies):
        raise PolicyError(
            f"{operation} is disabled by your organisation's security policy (disable_network)"
        )


def check_updates_allowed(*, policies: dict | None = None) -> None:
    """Raise PolicyError if administrator policy disables updates or network access."""
    if get("disable_updates", False, policies=policies) or get("disable_network", False, policies=policies):
        raise PolicyError(
            "Updates are disabled by your organisation's security policy"
        )


def requires_make_safe_on_open(*, policies: dict | None = None) -> bool:
    """True when administrator policy requires Make Safe sanitization on document open."""
    return bool(get("require_make_safe_on_open", False, policies=policies))


def forced_security_profile(*, policies: dict | None = None) -> str:
    """Return the administrator-mandated security profile name, or ``""`` if none."""
    return str(get("force_security_profile", "", policies=policies) or "").strip()


def check_security_profile_allowed(requested: str | None = None, *, policies: dict | None = None) -> str:
    """Validate ``requested`` against ``force_security_profile`` and return the effective profile name.

    If ``force_security_profile`` is configured, any non-matching profile request
    raises ``PolicyError`` so users/CLI/API cannot downgrade to a weaker profile.
    """
    forced = forced_security_profile(policies=policies)
    req = str(requested or "").strip()
    if not forced:
        return req
    if not req or req.lower() == forced.lower():
        return forced
    raise PolicyError(
        f"Security profile {req!r} is blocked by administrator policy; "
        f"only the mandated profile {forced!r} may be applied"
    )


_TRUST_CERT_SUFFIXES = frozenset({".pem", ".crt", ".cer", ".der"})


def validated_trust_list_paths(*, policies: dict | None = None) -> list[Path]:
    """Return validated certificate file paths from ``trust_list_paths``.

    Rejects relative paths, ``..`` traversal components, missing/unreadable
    targets, and symbolic links that escape their configured directory.
    Never raises on inaccessible entries; fails safely by omitting them.
    """
    raw_paths = get("trust_list_paths", [], policies=policies) or []
    if not isinstance(raw_paths, list):
        return []
    validated: list[Path] = []
    seen: set[Path] = set()
    for entry in raw_paths:
        text = str(entry or "").strip()
        if not text:
            continue
        p = Path(text)
        # Require absolute path and forbid raw '..' traversal segments.
        if not p.is_absolute() or ".." in p.parts:
            continue
        try:
            # Reject direct symlinks that escape the configured parent directory.
            parent_resolved = p.parent.resolve(strict=True)
            resolved = p.resolve(strict=True)
        except OSError:
            continue
        if p.is_symlink():
            try:
                resolved.relative_to(parent_resolved)
            except ValueError:
                continue
        if resolved.is_file():
            if resolved.suffix.lower() in _TRUST_CERT_SUFFIXES and resolved not in seen:
                seen.add(resolved)
                validated.append(resolved)
        elif resolved.is_dir():
            try:
                children = sorted(resolved.iterdir())
            except OSError:
                continue
            for child in children:
                if child.suffix.lower() not in _TRUST_CERT_SUFFIXES:
                    continue
                try:
                    c_res = child.resolve(strict=True)
                    c_res.relative_to(resolved)
                except (OSError, ValueError):
                    continue
                if c_res.is_file() and c_res not in seen:
                    seen.add(c_res)
                    validated.append(c_res)
    return validated


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
