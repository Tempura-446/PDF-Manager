# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Process-isolated job execution with timeout and memory limits (Security Phase 8).

Runs untrusted PDF processing work in a spawned child process so a hostile
document that crashes native code, loops forever, or allocates runaway memory
cannot bring down the parent ZPDF process.
"""

from __future__ import annotations

import importlib
import multiprocessing
from queue import Empty

from pdfmanager import core


class IsolationError(core.PdfManagerError):
    """Raised when an isolated worker crashes or violates resource limits."""


class IsolationTimeoutError(IsolationError):
    """Raised when an isolated worker exceeds its wall-clock time limit."""


def _child_entry(func_path: str, args: tuple, kwargs: dict, memory_mb: int | None, out_q) -> None:
    try:
        if memory_mb is not None and memory_mb > 0:
            try:
                import resource

                limit_bytes = int(memory_mb) * 1024 * 1024
                soft, hard = resource.getrlimit(resource.RLIMIT_AS)
                if hard == resource.RLIM_INFINITY or limit_bytes <= hard:
                    resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
            except (ImportError, ValueError, OSError):
                pass  # Windows or restricted container where RLIMIT_AS is unsupported

        mod_name, _, attr_name = func_path.partition(":")
        if not mod_name or not attr_name:
            raise ValueError(f"invalid callable path {func_path!r}; expected 'module:function'")
        module = importlib.import_module(mod_name)
        target = module
        for part in attr_name.split("."):
            target = getattr(target, part)
        result = target(*args, **kwargs)
        out_q.put(("ok", result))
    except MemoryError:
        out_q.put(("mem", "worker exceeded its memory limit"))
    except BaseException as exc:  # noqa: BLE001
        out_q.put(("err", f"{type(exc).__name__}: {exc}"))


def run_isolated(
    func_path: str,
    *args,
    timeout: float = 60.0,
    memory_mb: int | None = None,
    **kwargs,
):
    """Execute ``module:function(*args, **kwargs)`` in a spawned child process.

    Enforces a wall-clock ``timeout`` (seconds) and optional ``memory_mb``
    address-space limit (POSIX ``RLIMIT_AS``). Raises ``IsolationTimeoutError``
    on timeout and ``IsolationError`` on crash, OOM, or unhandled exception.
    """
    ctx = multiprocessing.get_context("spawn")
    out_q = ctx.Queue(maxsize=1)
    proc = ctx.Process(
        target=_child_entry,
        args=(func_path, args, kwargs, memory_mb, out_q),
        daemon=True,
    )
    proc.start()
    proc.join(timeout=timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2.0)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2.0)
        raise IsolationTimeoutError(
            f"isolated job {func_path!r} timed out after {timeout:.1f}s and was stopped"
        )

    try:
        status, payload = out_q.get_nowait()
    except Empty:
        code = proc.exitcode
        raise IsolationError(
            f"isolated job {func_path!r} crashed unexpectedly (exit code {code})"
        ) from None

    if status == "ok":
        return payload
    if status == "mem":
        raise IsolationError(f"isolated job {func_path!r} stopped: {payload}")
    raise IsolationError(f"isolated job {func_path!r} failed: {payload}")
