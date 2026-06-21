"""
watch_folders.py — ChaseOS Phase 8 Pass 9
Watched-folder automation for the Connector / Capture layer.

Monitors one or more local directories for newly dropped files and routes them
through the standard ChaseOS capture pipeline into quarantine.

==============================================================================
ARCHITECTURAL DECISIONS (hard choices documented here)
==============================================================================

CONFIG LOCATION:
    <vault_root>/.chaseos/watch_folders.json

    Co-located with dedup_registry.json in the .chaseos/ tool-state directory.
    Same convention as .git/, .venv/ — tool state belongs near the vault root,
    not scattered. Human-readable JSON. One file for all watched folder definitions.

PROCESSED-FILE REGISTRY:
    <vault_root>/.chaseos/watch_processed.json

    Tracks already-processed files by absolute path with mtime + size fingerprint.

    Record key: absolute path string.
    Record value: {mtime: float, size: int, processed_at: ISO8601}

    Decision: track path + mtime + size (not content SHA-256).

    Rationale:
        - Answers "have I handled this exact file version?" not "is this content known?"
        - Fast: no file read needed to check already-processed files
        - If the file changes (mtime/size differs), it is re-scanned; the dedup registry
          will catch content-level duplicates if the content is unchanged
        - The two layers serve distinct purposes:
            watch_processed: "did I already handle THIS file?"
            dedup_registry:  "is THIS CONTENT already in quarantine?"

SUPPORTED EXTENSIONS (Pass 9):
    .txt  → capture_from_cli() with capture_method="watched-folder"
    .md   → capture_from_cli() with capture_method="watched-folder"
    .html → capture_from_browser() for HTML→markdown extraction

    Unsupported types are reported and skipped.
    NOT supported: .pdf, .docx, .jpg, .png, OCR, JS-rendered pages.

DEFAULT CAPTURE BEHAVIOR:
    source_platform = "watched-folder"   (unless folder def overrides)
    capture_method  = "watched-folder"
    input_class     = folder's configured input_class (required at add time)
    title           = filename stem (normalized: hyphens/underscores → spaces)
                      For HTML files: HTML <title> or <h1> takes precedence over stem.

POLLING MODEL:
    --once      — single scan; primary mode; most composable
    --interval N — lightweight loop: scan, sleep N seconds, repeat
    No OS-native filesystem event hooks. No daemon/service installation.
    The --once mode is what cron, task scheduler, or manual invocation uses.

QUARANTINE DOCTRINE:
    All captures write to 03_INPUTS/00_QUARANTINE/[class]/.
    No SIC trigger. No auto-promotion.
    Pipeline: drop file → watch scan → ContentPacket → capture_content() → quarantine.

FAILURE MODEL:
    - Fail-safe per-file: one bad file does not stop the whole scan
    - Missing folders surfaced as warnings, not fatal errors
    - Disabled folders skipped without output
    - Malformed HTML reported as error, not crash
    - Already-processed unchanged files silently skipped (no noise)

HONEST LIMITATIONS:
    - Polling only (no inotify/FSEvents/ReadDirectoryChangesW)
    - No daemon/service installation
    - No PDF, DOCX, OCR ingestion
    - No live URL fetching
    - No Grok connector
    - No SIC auto-ingestion
    - No recursive subdirectory scanning (top-level files only in Pass 9)
    - No promotion — quarantine only
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capture import capture_content
from .connectors.cli_connector import capture_from_cli
from .connectors.browser_connector import capture_from_browser


# ── Constants ──────────────────────────────────────────────────────────────────

_CHASEOS_STATE_DIR       = ".chaseos"
_WATCH_CONFIG_FILENAME   = "watch_folders.json"
_WATCH_PROCESSED_FILENAME = "watch_processed.json"
_CONFIG_SCHEMA_VERSION   = "1.0"
_PROCESSED_SCHEMA_VERSION = "1.0"

# Supported file extensions in Pass 9.
# .txt/.md → cli_connector path; .html → browser_connector path.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".html"})

# Hard ceiling on watched-file size — files larger than this are skipped and
# recorded as FileSkipped(reason="file_too_large") rather than read into memory.
MAX_WATCHED_FILE_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB

# Default values for new watched-folder captures
DEFAULT_SOURCE_PLATFORM = "watched-folder"
DEFAULT_CAPTURE_METHOD  = "watched-folder"


# ── Path helpers ───────────────────────────────────────────────────────────────

def config_path(vault_root: Path) -> Path:
    """<vault_root>/.chaseos/watch_folders.json"""
    return vault_root / _CHASEOS_STATE_DIR / _WATCH_CONFIG_FILENAME


def processed_path(vault_root: Path) -> Path:
    """<vault_root>/.chaseos/watch_processed.json"""
    return vault_root / _CHASEOS_STATE_DIR / _WATCH_PROCESSED_FILENAME


# ── Watch-folder config I/O ────────────────────────────────────────────────────

def load_config(vault_root: Path) -> dict:
    """
    Load watch_folders.json config.

    Returns empty config dict if file absent or corrupt (fail-open).
    """
    path = config_path(vault_root)
    if not path.exists():
        return _empty_config()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_config()


def save_config(vault_root: Path, config: dict) -> None:
    """Persist watch_folders.json config to disk."""
    path = config_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def _empty_config() -> dict:
    return {
        "schema_version": _CONFIG_SCHEMA_VERSION,
        "folders": [],
    }


# ── Processed-file registry I/O ───────────────────────────────────────────────

def load_processed(vault_root: Path) -> dict:
    """
    Load watch_processed.json registry.

    Returns empty registry if absent or corrupt (fail-open).
    """
    path = processed_path(vault_root)
    if not path.exists():
        return _empty_processed()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_processed()


def save_processed(vault_root: Path, processed: dict) -> None:
    """Persist watch_processed.json registry to disk."""
    path = processed_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(processed, indent=2, ensure_ascii=False), encoding="utf-8")


def _empty_processed() -> dict:
    return {
        "schema_version": _PROCESSED_SCHEMA_VERSION,
        "processed": {},
    }


def is_processed(abs_path: str, stat_mtime: float, stat_size: int, processed: dict) -> bool:
    """
    Return True if this exact file version (path + mtime + size) was already processed.

    If the file was modified (mtime or size changed), returns False so it gets re-scanned.
    """
    entry = processed.get("processed", {}).get(abs_path)
    if entry is None:
        return False
    return entry.get("mtime") == stat_mtime and entry.get("size") == stat_size


def mark_processed(abs_path: str, stat_mtime: float, stat_size: int, processed: dict) -> None:
    """Record this file version as processed in the in-memory registry dict."""
    if "processed" not in processed:
        processed["processed"] = {}
    processed["processed"][abs_path] = {
        "mtime":        stat_mtime,
        "size":         stat_size,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Folder CRUD ────────────────────────────────────────────────────────────────

def add_folder(
    vault_root: Path,
    folder_path: str,
    input_class: str,
    *,
    source_platform: str = DEFAULT_SOURCE_PLATFORM,
    extensions: list[str] | None = None,
    workspace_hint: str | None = None,
    domain_hint: str | None = None,
    project_hint: str | None = None,
    topic_hint: str | None = None,
    origin_kind: str | None = None,
    desired_output_kind: str | None = None,
) -> dict:
    """
    Add a watched folder to the config.

    If a folder with the same resolved path already exists, it is replaced.

    Args:
        vault_root:          Vault root path.
        folder_path:         Absolute or relative path to the folder to watch.
        input_class:         Required. ChaseOS input_class for all files in this folder.
        source_platform:     Source platform tag (default: "watched-folder").
        extensions:          Extension allowlist (default: all SUPPORTED_EXTENSIONS).
                             Pass an empty list to use all supported extensions.
        workspace_hint:      Optional SIC workspace hint (stored in sidecar; hint only).
        domain_hint:         ChaseOS domain hint (hint only).
        project_hint:        Active project hint (hint only).
        topic_hint:          Subject label hint (hint only).
        origin_kind:         Content authorship origin hint.
        desired_output_kind: Intended output type hint.

    Returns:
        The created folder definition dict.

    Raises:
        ValueError: If input_class is empty or folder_path is empty.
    """
    if not folder_path or not folder_path.strip():
        raise ValueError("folder_path must not be empty.")
    if not input_class or not input_class.strip():
        raise ValueError("input_class must not be empty.")

    from .content_packet import VALID_INPUT_CLASSES
    if input_class not in VALID_INPUT_CLASSES:
        raise ValueError(
            f"Unknown input_class '{input_class}'. "
            f"Valid classes: {sorted(VALID_INPUT_CLASSES)}"
        )

    # Resolve to absolute path for storage
    resolved_path = str(Path(folder_path).resolve())

    # Validate extensions against SUPPORTED_EXTENSIONS
    if extensions:
        normalized_exts = [e if e.startswith(".") else f".{e}" for e in extensions]
        invalid = [e for e in normalized_exts if e not in SUPPORTED_EXTENSIONS]
        if invalid:
            raise ValueError(
                f"Unsupported extensions: {invalid}. "
                f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
            )
        ext_list = normalized_exts
    else:
        ext_list = sorted(SUPPORTED_EXTENSIONS)

    config = load_config(vault_root)
    folders = config.get("folders", [])

    # Remove existing entry with same path (idempotent replace)
    folders = [f for f in folders if f.get("path") != resolved_path]

    folder_def: dict[str, Any] = {
        "path":                 resolved_path,
        "enabled":              True,
        "input_class":          input_class,
        "source_platform":      source_platform,
        "extensions":           ext_list,
        "workspace_hint":       workspace_hint,
        "domain_hint":          domain_hint,
        "project_hint":         project_hint,
        "topic_hint":           topic_hint,
        "origin_kind":          origin_kind,
        "desired_output_kind":  desired_output_kind,
        "added_at":             datetime.now(timezone.utc).isoformat(),
    }

    folders.append(folder_def)
    config["folders"] = folders
    save_config(vault_root, config)
    return folder_def


def remove_folder(vault_root: Path, folder_path: str) -> bool:
    """
    Remove a watched folder from the config by path.

    Returns True if removed, False if not found.
    """
    resolved_path = str(Path(folder_path).resolve())
    config = load_config(vault_root)
    folders = config.get("folders", [])
    new_folders = [f for f in folders if f.get("path") != resolved_path]
    if len(new_folders) == len(folders):
        return False  # Not found
    config["folders"] = new_folders
    save_config(vault_root, config)
    return True


def set_folder_enabled(vault_root: Path, folder_path: str, enabled: bool) -> bool:
    """
    Enable or disable a watched folder by path.

    Returns True if updated, False if not found.
    """
    resolved_path = str(Path(folder_path).resolve())
    config = load_config(vault_root)
    folders = config.get("folders", [])
    updated = False
    for f in folders:
        if f.get("path") == resolved_path:
            f["enabled"] = enabled
            updated = True
            break
    if updated:
        config["folders"] = folders
        save_config(vault_root, config)
    return updated


def list_folders(vault_root: Path) -> list[dict]:
    """Return the list of all configured watched folder definitions."""
    config = load_config(vault_root)
    return config.get("folders", [])


# ── Scan result types ──────────────────────────────────────────────────────────

@dataclass
class FileCaptured:
    """A file was successfully captured to quarantine."""
    file_path:  Path
    result:     dict    # from capture_content()


@dataclass
class FileDuplicate:
    """A file's content was already in the dedup registry; not re-captured."""
    file_path:  Path
    result:     dict    # from capture_content() with is_duplicate=True


@dataclass
class FileSkipped:
    """A file was skipped (unsupported extension or already processed)."""
    file_path:  Path
    reason:     str     # "unsupported_extension" | "already_processed"


@dataclass
class FileError:
    """A file failed to load or capture."""
    file_path:  Path
    error:      str


@dataclass
class FolderScanResult:
    """Aggregated result of scanning one watched folder."""
    folder_path:  Path
    enabled:      bool
    exists:       bool
    folder_error: str | None          = None  # set if folder missing/unreadable
    captured:     list[FileCaptured]  = field(default_factory=list)
    duplicates:   list[FileDuplicate] = field(default_factory=list)
    skipped:      list[FileSkipped]   = field(default_factory=list)
    errors:       list[FileError]     = field(default_factory=list)


# ── Title derivation for non-HTML files ───────────────────────────────────────

def _title_from_stem(file_path: Path) -> str:
    """Derive a human title from the filename stem (hyphens/underscores → spaces)."""
    stem = file_path.stem
    return stem.replace("-", " ").replace("_", " ").strip() or file_path.name


# ── Per-file capture routing ───────────────────────────────────────────────────

def _capture_file(file_path: Path, folder_def: dict) -> dict:
    """
    Build a ContentPacket from a single discovered file and call capture_content().

    Routes:
        .txt / .md → capture_from_cli()
        .html      → capture_from_browser()

    Returns the capture_content() result dict.
    Raises on load or capture errors (caller must catch).
    """
    ext = file_path.suffix.lower()

    input_class      = folder_def["input_class"]
    source_platform  = folder_def.get("source_platform", DEFAULT_SOURCE_PLATFORM)
    workspace_hint   = folder_def.get("workspace_hint")
    domain_hint      = folder_def.get("domain_hint")
    project_hint     = folder_def.get("project_hint")
    topic_hint       = folder_def.get("topic_hint")
    origin_kind      = folder_def.get("origin_kind")
    desired_output_kind = folder_def.get("desired_output_kind")

    if ext in (".txt", ".md"):
        # Text/markdown path → cli_connector
        title = _title_from_stem(file_path)
        packet = capture_from_cli(
            input_class=input_class,
            source_platform=source_platform,
            title=title,
            file_path=str(file_path),
            workspace_hint=workspace_hint,
            domain_hint=domain_hint,
            project_hint=project_hint,
            topic_hint=topic_hint,
            origin_kind=origin_kind,
            desired_output_kind=desired_output_kind,
            capture_method=DEFAULT_CAPTURE_METHOD,
        )
    elif ext == ".html":
        # HTML path → browser_connector (extracts title from HTML)
        packet = capture_from_browser(
            file_path=str(file_path),
            title=None,  # auto-extract from HTML
            source_url=None,
            input_class=input_class,
            source_platform=source_platform,
            workspace_hint=workspace_hint,
            domain_hint=domain_hint,
            project_hint=project_hint,
            topic_hint=topic_hint,
            origin_kind=origin_kind,
            desired_output_kind=desired_output_kind,
        )
    else:
        # Should not reach here — callers filter by extension first
        raise ValueError(f"Unsupported file extension: {ext}")

    return packet


# ── Folder scan ────────────────────────────────────────────────────────────────

def scan_folder(
    folder_def: dict,
    vault_root: Path,
    processed: dict,
) -> FolderScanResult:
    """
    Scan one watched folder definition for new files and route them to capture.

    Mutates `processed` in-memory to mark newly-handled files.
    Caller must call save_processed() after scanning all folders.

    Args:
        folder_def:  One folder definition dict from load_config().
        vault_root:  Vault root for capture_content().
        processed:   In-memory watch_processed dict (mutated in-place).

    Returns:
        FolderScanResult with per-file outcomes.
    """
    folder_path = Path(folder_def["path"])
    enabled     = folder_def.get("enabled", True)
    extensions  = set(folder_def.get("extensions") or sorted(SUPPORTED_EXTENSIONS))

    result = FolderScanResult(
        folder_path=folder_path,
        enabled=enabled,
        exists=folder_path.exists(),
    )

    # Disabled — skip without scanning
    if not enabled:
        return result

    # Missing folder — warn but don't crash
    if not folder_path.exists():
        result.folder_error = f"folder does not exist: {folder_path}"
        return result

    # Scan top-level files only (no recursive subdirectory scanning in Pass 9)
    try:
        entries = list(folder_path.iterdir())
    except OSError as exc:
        result.folder_error = f"cannot read folder: {exc}"
        return result

    for entry in sorted(entries):
        if not entry.is_file():
            continue  # skip subdirectories

        ext = entry.suffix.lower()

        # Extension filter — check against folder's allowed set
        if ext not in extensions:
            result.skipped.append(FileSkipped(file_path=entry, reason="unsupported_extension"))
            continue

        # Processed-file check (path + mtime + size)
        try:
            stat = entry.stat()
        except OSError as exc:
            result.errors.append(FileError(file_path=entry, error=f"stat failed: {exc}"))
            continue

        abs_path = str(entry.resolve())
        if is_processed(abs_path, stat.st_mtime, stat.st_size, processed):
            # Already handled at this version — skip silently (not noise)
            continue

        # Size guard — skip oversized files before reading content into memory
        if stat.st_size > MAX_WATCHED_FILE_SIZE_BYTES:
            result.skipped.append(FileSkipped(file_path=entry, reason="file_too_large"))
            mark_processed(abs_path, stat.st_mtime, stat.st_size, processed)
            continue

        # Attempt capture
        try:
            packet = _capture_file(entry, folder_def)
            capture_result = capture_content(packet, vault_root=vault_root)
        except Exception as exc:
            result.errors.append(FileError(file_path=entry, error=str(exc)))
            # Still mark as processed to avoid infinite error loop on bad files
            # The error is reported; the file won't be retried on next run unless changed.
            mark_processed(abs_path, stat.st_mtime, stat.st_size, processed)
            continue

        # Mark processed regardless of dedup outcome
        mark_processed(abs_path, stat.st_mtime, stat.st_size, processed)

        if capture_result.get("is_duplicate"):
            result.duplicates.append(FileDuplicate(file_path=entry, result=capture_result))
        else:
            result.captured.append(FileCaptured(file_path=entry, result=capture_result))

    return result


def scan_all_folders(
    vault_root: Path,
    *,
    include_disabled: bool = False,
) -> list[FolderScanResult]:
    """
    Scan all configured watched folders.

    Loads config and processed registry, scans each enabled folder,
    persists the updated processed registry.

    Args:
        vault_root:       Vault root path.
        include_disabled: If True, scan even disabled folders (for reporting).

    Returns:
        List of FolderScanResult, one per configured folder.
    """
    config    = load_config(vault_root)
    processed = load_processed(vault_root)
    folders   = config.get("folders", [])

    results: list[FolderScanResult] = []
    for folder_def in folders:
        if not folder_def.get("enabled", True) and not include_disabled:
            # Add a silent disabled result for output summary
            results.append(FolderScanResult(
                folder_path=Path(folder_def["path"]),
                enabled=False,
                exists=Path(folder_def["path"]).exists(),
            ))
            continue
        scan_result = scan_folder(folder_def, vault_root, processed)
        results.append(scan_result)

    # Persist processed registry (mutated in-place by scan_folder calls)
    save_processed(vault_root, processed)

    return results
