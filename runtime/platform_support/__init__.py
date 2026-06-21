"""ChaseOS cross-platform host helpers.

Single home for the platform-conditional logic that the dual-boot audit found
scattered across the runtime (interpreter resolution, console-window
suppression, reveal-in-file-manager, autostart backend selection,
single-instance guarding). New cross-platform code should call these helpers
instead of re-deriving ``os.name`` / ``sys.platform`` branches ad hoc.

Design rules:
- Pure stdlib, no third-party imports.
- No side effects at import time.
- Never hard-fail on the "wrong" OS: every helper degrades to a safe default.

This module is additive. It does not replace existing working callsites; it is
the canonical place for *new* portable code and the target for future
consolidation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

WINDOWS = "windows"
LINUX = "linux"
MACOS = "macos"
OTHER = "other"


# OS/platform are read through these getters so tests can simulate another OS
# without mutating the real ``os``/``sys`` modules (which would break ``pathlib``
# Path instantiation on the host running the tests).
def _os_name() -> str:
    return os.name


def _sys_platform() -> str:
    return sys.platform


# ── OS detection ─────────────────────────────────────────────────────────────
def current_os() -> str:
    """Return one of ``windows`` / ``linux`` / ``macos`` / ``other``."""
    if _os_name() == "nt":
        return WINDOWS
    platform = _sys_platform()
    if platform.startswith("linux"):
        return LINUX
    if platform == "darwin":
        return MACOS
    return OTHER


def is_windows() -> bool:
    return _os_name() == "nt"


def is_macos() -> bool:
    return _os_name() != "nt" and _sys_platform() == "darwin"


def is_wsl() -> bool:
    """True when running inside a WSL distro (POSIX kernel reporting microsoft)."""
    if _os_name() == "nt":
        return False
    if str(os.environ.get("WSL_DISTRO_NAME") or "").strip():
        return True
    for probe in ("/proc/sys/kernel/osrelease", "/proc/version"):
        try:
            text = Path(probe).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if "microsoft" in text or "wsl" in text:
            return True
    return False


# ── Subprocess console suppression (Windows) ─────────────────────────────────
def no_window_subprocess_kwargs(*, detached: bool = False) -> dict[str, Any]:
    """Return subprocess kwargs that suppress transient Windows consoles.

    On non-Windows hosts this is an empty dict (there is no console to hide).
    Pair ``CREATE_NO_WINDOW`` with hidden ``STARTUPINFO``; add
    ``DETACHED_PROCESS`` only for long-lived detached daemon launches.
    """
    if _os_name() != "nt":
        return {}
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if detached:
        flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0) or 0)
    kwargs: dict[str, Any] = {"creationflags": flags}
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0)
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0) or 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


# ── Interpreter resolution ───────────────────────────────────────────────────
def venv_python_candidates(project_root: Path, *, prefer_gui: bool = False) -> list[Path]:
    """Return ordered interpreter candidates for the current OS.

    Windows: pythonw.exe (if ``prefer_gui``) then python.exe across the known
    venv layouts (``.venv``, ``.venv-win314``, ``.venv-win``).
    POSIX: ``.venv/bin/python`` then ``python3``.
    """
    root = project_root if isinstance(project_root, Path) else Path(project_root)
    if _os_name() == "nt":
        win_venvs = (".venv", ".venv-win314", ".venv-win")
        names = (("pythonw.exe", "python.exe") if prefer_gui else ("python.exe",))
        return [root / v / "Scripts" / name for name in names for v in win_venvs]
    return [root / ".venv" / "bin" / "python", root / ".venv" / "bin" / "python3"]


def project_python_executable(project_root: Path, *, prefer_gui: bool = False) -> str:
    """Resolve the project's venv interpreter, falling back to ``sys.executable``.

    Never raises: if no project venv interpreter exists, returns the running
    interpreter so callers always get a usable path.
    """
    for candidate in venv_python_candidates(project_root, prefer_gui=prefer_gui):
        if candidate.exists():
            return str(candidate.resolve())
    return sys.executable


# ── Reveal in OS file manager ────────────────────────────────────────────────
def reveal_in_file_manager(path: Path) -> dict[str, Any]:
    """Open the OS file manager focused on ``path`` (best effort).

    Returns ``{ok, method, error}``. Never raises: a missing file manager is a
    soft failure, not a crash.
    """
    target = path if isinstance(path, Path) else Path(path)
    parent = target if target.is_dir() else target.parent
    try:
        if _os_name() == "nt":
            subprocess.Popen(
                ["explorer.exe", "/select,", str(target)],
                **no_window_subprocess_kwargs(),
            )
            return {"ok": True, "method": "explorer", "error": None}
        if _sys_platform() == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
            return {"ok": True, "method": "open", "error": None}
        subprocess.Popen(["xdg-open", str(parent)])
        return {"ok": True, "method": "xdg-open", "error": None}
    except Exception as exc:  # FileNotFoundError, OSError, etc.
        return {"ok": False, "method": None, "error": str(exc)}


# ── Autostart backend selection ──────────────────────────────────────────────
def default_autostart_kind() -> str:
    """Return the native autostart/registration backend id for the current OS.

    - ``windows-task-scheduler`` on Windows
    - ``launchd`` on macOS
    - ``systemd-user`` on Linux when ``systemctl --user`` is plausibly available,
      otherwise ``cron``
    """
    if _os_name() == "nt":
        return "windows-task-scheduler"
    if _sys_platform() == "darwin":
        return "launchd"
    import shutil

    if shutil.which("systemctl"):
        return "systemd-user"
    return "cron"


def supported_autostart_kinds() -> tuple[str, ...]:
    """All registration kinds ChaseOS knows how to generate artifacts for."""
    return ("windows-task-scheduler", "systemd-user", "cron")


# ── Vault-relative path persistence (dual-boot state portability) ─────────────
def to_vault_relative(path: Path | str, vault_root: Path | str) -> str:
    """Return ``path`` as a POSIX-style path relative to ``vault_root``.

    State files that persist absolute paths break when the same vault is opened
    from the other boot of a dual-boot machine (``C:\\Users\\...`` vs
    ``/home/...``/``/mnt/c/...``). Storing vault-relative POSIX strings makes the
    record portable. If ``path`` is not under ``vault_root`` (e.g. a log outside
    the vault), the original absolute path string is returned unchanged.
    """
    p = (path if isinstance(path, Path) else Path(path))
    root = (vault_root if isinstance(vault_root, Path) else Path(vault_root))
    try:
        rel = p.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return str(p)
    return rel.as_posix()


def resolve_vault_path(stored: str, vault_root: Path | str) -> Path:
    """Inverse of :func:`to_vault_relative`.

    Joins a stored vault-relative path onto the *current* ``vault_root`` (so it
    resolves correctly on whichever OS is booted). Absolute stored paths are
    returned as-is for backward compatibility with legacy records.
    """
    root = (vault_root if isinstance(vault_root, Path) else Path(vault_root))
    candidate = Path(stored)
    if candidate.is_absolute():
        return candidate
    return (root / candidate)


# ── Single-instance guard (POSIX file lock) ──────────────────────────────────
def _posix_flock_nonblocking(handle: Any) -> bool:
    """Take a non-blocking exclusive ``flock`` on ``handle``. POSIX-only.

    Returns True if the lock was acquired, False if another process holds it.
    ``fcntl`` is imported lazily because it does not exist on Windows.
    """
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire_single_instance(lock_path: Path | str) -> tuple[bool, Any]:
    """Best-effort cross-process single-instance guard.

    Returns ``(acquired, handle)``. The Windows desktop shell already guards
    duplicate launches with a named mutex, so on Windows this returns
    ``(True, None)`` (no-op — the mutex path owns that responsibility). On POSIX
    it takes a non-blocking ``flock`` on ``lock_path``; **the returned handle
    must be kept alive** for the lock to persist (closing it releases the lock).
    Fails open (``(True, None)``) if locking is unavailable.
    """
    if _os_name() == "nt":
        return True, None
    try:
        path = lock_path if isinstance(lock_path, Path) else Path(lock_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "w")
    except OSError:
        return True, None  # cannot create lock file — do not block startup
    if not _posix_flock_nonblocking(handle):
        try:
            handle.close()
        except OSError:
            pass
        return False, None
    try:
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass
    return True, handle
