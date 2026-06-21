"""ChaseOS Core publish-readiness invariants.

Guards the MIT-Core boundary so future changes cannot silently couple Core to the
proprietary layers or ship personal/proprietary content:

1. every shipped Core runtime package imports cleanly (stdlib-first);
2. no shipped runtime source has a top-level (col-0, non-test) import of an excluded
   proprietary module;
3. no proprietary runtime packages (studio / commerce / forge) are present.

Run: ``pytest test_core_publish_readiness.py`` from the repo root.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"

# Modules that must NEVER be a compile-time dependency of MIT Core.
EXCLUDED = (
    "runtime.studio",
    "runtime.commerce",
    "runtime.forge",
    "runtime.aor",
    "runtime.mcp",
    "runtime.workflows",
    "runtime.operator_surface",
    "runtime.events",
    "runtime.providers",
    "runtime.siteops",
    "runtime.chaseos_gate",
    "runtime.cli",
    "runtime.config",
    "runtime.hermes",
    "runtime.openclaw",
)

# Core packages that must import cleanly with the standard library only.
CORE_PACKAGES = (
    "runtime.agent_bus",
    "runtime.schedules",
    "runtime.net",
    "runtime.security",
    "runtime.platform_support",
    "runtime.context",
    "runtime.lifecycle",
    "runtime.common",
    "runtime.adapters",
    "runtime.execution_adapters.model_config",
    "runtime.source_intelligence",
    "runtime.memory",
    "runtime.capture",
    "runtime.schemas",
    "runtime.core_export",
    "runtime.install_safety",
    "runtime.audit_writeback",
    "runtime.installer",
    "runtime.voice",
)


def _py_files():
    for path in RUNTIME.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        yield path


def _top_level_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    mods: set[str] = set()
    for node in tree.body:  # tree.body == column-0 statements only
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module)
    return mods


@pytest.mark.parametrize("module", CORE_PACKAGES)
def test_core_package_imports(module: str) -> None:
    importlib.import_module(module)


def test_no_proprietary_top_level_imports() -> None:
    offenders: list[str] = []
    for path in _py_files():
        for mod in _top_level_imports(path):
            if any(mod == ex or mod.startswith(ex + ".") for ex in EXCLUDED):
                offenders.append(f"{path.relative_to(ROOT).as_posix()} -> {mod}")
    assert not offenders, "Excluded top-level imports in MIT Core:\n" + "\n".join(sorted(offenders))


def test_no_proprietary_packages_present() -> None:
    for name in ("studio", "commerce", "forge"):
        assert not (RUNTIME / name).exists(), f"runtime/{name}/ must not be in MIT Core"


def test_no_instance_memory_data() -> None:
    """Runtime memory is per-instance state, not framework. Core ships only the memory
    code + schemas + READMEs — never populated per-runtime profiles, identity ledgers,
    scorecards, repair, or nav data."""
    mem = RUNTIME / "memory"
    if not mem.exists():
        return
    offenders: list[str] = []
    adapters = mem / "adapters"
    if adapters.exists():
        offenders += [
            f"runtime/memory/adapters/{c.name}/ (per-runtime data dir)"
            for c in adapters.iterdir()
            if c.is_dir() and c.name != "__pycache__"
        ]
    for pattern, keep in (("scorecards/*.json", None), ("repair/*.json", "_schema.json")):
        for path in mem.glob(pattern):
            if keep and path.name == keep:
                continue
            offenders.append(path.relative_to(ROOT).as_posix())
    offenders += [p.relative_to(ROOT).as_posix() for p in mem.glob("nav/**/*.json")]
    assert not offenders, "Per-instance memory data must not ship in Core:\n" + "\n".join(sorted(offenders))


def test_no_tracked_bytecode() -> None:
    """No compiled bytecode (.pyc / __pycache__) should be committed (tracked)."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    tracked = [line for line in out.splitlines() if line.endswith(".pyc") or "__pycache__/" in line]
    assert not tracked, "Compiled bytecode is tracked:\n" + "\n".join(tracked)


def test_no_personal_paths() -> None:
    """No real personal/instance paths in tracked files. Generic placeholder examples
    (e.g. C:\\Users\\alice\\...) are fine; the user's home + vault name are not."""
    import re
    import subprocess

    markers = re.compile(r"chaseos_obsidian|Users[\\/]chaseos[\\/]|/home/chaseos\b", re.IGNORECASE)
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    offenders = []
    for rel in out.splitlines():
        if rel.endswith("test_core_publish_readiness.py"):
            continue  # this guard file legitimately contains the detection patterns
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if markers.search(text):
            offenders.append(rel)
    assert not offenders, "Personal/instance paths in Core:\n" + "\n".join(sorted(offenders))
