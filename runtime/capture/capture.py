"""
capture.py — ChaseOS Phase 8 Pass 6
Public API and backward-compat CLI for the Connector / Capture layer.

CANONICAL CLI (Phase 8 Pass 2+):
    chaseos capture file PATH --class CLASS --source SOURCE --title "..."
    chaseos capture stdin --class CLASS --source SOURCE --title "..."
    chaseos intake ls
    chaseos doctor
    (see runtime/cli/main.py)

BACKWARD-COMPAT CLI (preserved, still fully functional):
    python -m runtime.capture.capture \\
        --input-class transcript \\
        --source-platform youtube \\
        --title "Order Flow and Market Microstructure" \\
        --file transcript.txt

    cat digest.txt | python -m runtime.capture.capture \\
        --input-class digest \\
        --source-platform perplexity \\
        --title "Crypto Perps Funding Rate Deep Dive"

PUBLIC API (stable, used by connectors and the CLI):
    capture_content(packet, vault_root) -> dict
        Write a ContentPacket to quarantine and return result dict.
        Result contains is_duplicate: True if already in dedup registry.

QUARANTINE — SIC HANDOFF DOCTRINE:
    All captures land in 03_INPUTS/00_QUARANTINE/[class]/ as raw quarantine.
    They are NOT ingested into SIC at capture time.
    Pipeline: capture -> quarantine -> human review -> Gate promotion -> SIC (later).
    This module does not know about, and must not trigger, SIC.

DEDUP REGISTRY (Phase 8 Pass 6):
    capture_content() checks the SHA-256 dedup registry before writing.
    If content already in registry: returns is_duplicate=True, no file written.
    If new: writes normally, registers SHA-256, returns is_duplicate=False.
    Registry location: <vault_root>/.chaseos/dedup_registry.json
    Registry is fail-open: if corrupt/missing, capture proceeds without dedup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .content_packet import ContentPacket, VALID_INPUT_CLASSES
from .connectors.cli_connector import capture_from_cli
from .intake_writer import write_intake
from .dedup_registry import (
    load_registry,
    save_registry,
    is_duplicate,
    get_entry,
    register_capture,
    build_registry_entry,
)


# ── Vault root detection ───────────────────────────────────────────────────────

def _detect_vault_root() -> Path:
    """
    Detect the vault root by walking up from this file's location.

    The vault root is the directory that contains 03_INPUTS/ and CLAUDE.md.
    This file lives at runtime/capture/capture.py, so vault root = parents[2].
    """
    here = Path(__file__).resolve()
    vault_root = here.parents[2]  # runtime/capture/capture.py -> vault root
    if not (vault_root / "03_INPUTS").exists():
        raise RuntimeError(
            f"Could not detect vault root. Expected 03_INPUTS/ at: {vault_root}\n"
            "Use --vault-root to specify the vault path explicitly."
        )
    return vault_root


# ── Public API ─────────────────────────────────────────────────────────────────

def capture_content(
    packet: ContentPacket,
    vault_root: Path | None = None,
    auto_link: bool = False,
) -> dict:
    """
    Write a ContentPacket to quarantine and return the result dict.

    This is the programmatic entry point for all connectors. Both the canonical
    `chaseos` CLI and the backward-compat `python -m runtime.capture.capture` path
    call this function.

    DEDUP BEHAVIOR (Phase 8 Pass 6):
        Before writing, computes SHA-256 of the content body and checks the
        dedup registry at <vault_root>/.chaseos/dedup_registry.json.

        If duplicate detected:
            - No quarantine file is written.
            - Returns result dict with is_duplicate=True and provenance of the
              first capture (capture_id, first_captured_at).
            - Operator is informed — duplicates are NOT silently discarded.

        If new content:
            - Writes normally to quarantine.
            - Registers SHA-256 in the dedup registry.
            - Returns normal result dict with is_duplicate=False.

        If registry is missing or corrupt:
            - Fails open: capture proceeds as if no duplicate exists.
            - A new registry is created on first successful write.

    Files are written to: 03_INPUTS/00_QUARANTINE/[class]/[filename].md
    Sidecar written to:   03_INPUTS/00_QUARANTINE/[class]/[filename].meta.json

    SIC is NOT triggered here. This is intake only.

    Args:
        packet:     Fully-populated ContentPacket.
        vault_root: Vault root path. Auto-detected if None.

    Returns:
        For a new capture (is_duplicate=False):
            {
                "is_duplicate":   False,
                "content_path":   str,
                "sidecar_path":   str,
                "filename":       str,
                "capture_id":     str,
                "content_sha256": str,
                "quarantine_dir": str,
            }

        For a duplicate (is_duplicate=True):
            {
                "is_duplicate":         True,
                "content_sha256":       str,
                "duplicate_of":         str,   # capture_id of first capture
                "original_captured_at": str,   # first capture timestamp
                "title":                str,
                "source_platform":      str,
                "input_class":          str,
            }
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    # Compute SHA-256 upfront (same algorithm as intake_writer.py)
    content_sha256 = hashlib.sha256(packet.content.encode("utf-8")).hexdigest()

    # Load dedup registry and check for duplicate
    registry = load_registry(vault_root)
    if is_duplicate(content_sha256, registry):
        existing = get_entry(content_sha256, registry) or {}
        return {
            "is_duplicate":         True,
            "content_sha256":       content_sha256,
            "duplicate_of":         existing.get("capture_id"),
            "original_captured_at": existing.get("first_captured_at"),
            "title":                packet.title,
            "source_platform":      packet.source_platform,
            "input_class":          packet.input_class,
        }

    # Prompt-injection scan (label-only; quarantine is the containment boundary).
    # Populates the sidecar `injection_scan` field so downstream consumers and the
    # operator can see when quarantined content carries instruction-like markers.
    try:
        from runtime.security.injection_scan import scan_label

        packet.injection_scan = scan_label(packet.content)
    except Exception:
        packet.injection_scan = "scan-error"

    # Not a duplicate — write to quarantine
    result = write_intake(packet, vault_root)

    # Register in dedup registry (first-capture wins)
    entry = build_registry_entry(
        content_sha256=result["content_sha256"],
        capture_id=result["capture_id"],
        first_captured_at=packet.captured_at,
        title=packet.title,
        source_platform=packet.source_platform,
        source_url=packet.source_url,
        input_class=packet.input_class,
        capture_method=packet.capture_method,
    )
    register_capture(result["content_sha256"], entry, registry)
    save_registry(vault_root, registry)

    result["is_duplicate"] = False

    # Post-capture hooks — fail-open, never affect the primary result
    try:
        from runtime.capture.post_capture_hooks import run_post_capture_hooks
        hooks = run_post_capture_hooks(
            origin_kind=getattr(packet, "origin_kind", "") or "",
            capture_result=result,
            vault_root=vault_root,
            auto_link=auto_link,
        )
        if hooks:
            result["post_capture_hooks"] = hooks
    except Exception:  # noqa: BLE001
        pass

    return result


# ── Backward-compat CLI ────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m runtime.capture.capture",
        description=(
            "ChaseOS Phase 8 — capture content into quarantine (backward-compat path). "
            "Canonical command: chaseos capture file PATH [options]"
        ),
    )
    p.add_argument(
        "--input-class",
        required=True,
        choices=sorted(VALID_INPUT_CLASSES),
        help="Intake class (determines quarantine subfolder)",
    )
    p.add_argument(
        "--source-platform",
        required=True,
        help="Source platform identifier (e.g. youtube, perplexity, web)",
    )
    p.add_argument(
        "--title",
        required=True,
        help="Human-readable title for the captured content",
    )
    p.add_argument("--source-url", default=None, help="URL of the original source")
    p.add_argument("--author", default=None, help="Author or creator")
    p.add_argument(
        "--file",
        default=None,
        metavar="PATH",
        help="Path to local text file (reads stdin if omitted)",
    )
    p.add_argument(
        "--vault-root",
        default=None,
        metavar="PATH",
        help="Override vault root path (auto-detected by default)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output result as JSON",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Backward-compat CLI entry point. Prefer `chaseos capture file` instead."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        packet = capture_from_cli(
            input_class=args.input_class,
            source_platform=args.source_platform,
            title=args.title,
            source_url=args.source_url,
            author=args.author,
            file_path=args.file,
            capture_method="cli",
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    vault_root = Path(args.vault_root) if args.vault_root else None

    try:
        result = capture_content(packet, vault_root=vault_root)
    except Exception as exc:
        print(f"ERROR writing intake: {exc}", file=sys.stderr)
        return 1

    if args.output_json:
        print(json.dumps(result, indent=2))
    elif result.get("is_duplicate"):
        print(f"DUPLICATE: {result.get('title', '(no title)')}")
        print(f"  Already captured: {result.get('original_captured_at', 'unknown')}")
        print(f"  Capture ID:       {result.get('duplicate_of', 'unknown')}")
        print(f"  SHA-256:          {result['content_sha256'][:16]}...")
        print()
        print("  Content not re-captured. Use --force (future) to override.")
    else:
        print(f"Captured: {result['filename']}")
        print(f"  Quarantine: {result['quarantine_dir']}")
        print(f"  Content:    {result['content_path']}")
        print(f"  Sidecar:    {result['sidecar_path']}")
        print(f"  ID:         {result['capture_id']}")
        print(f"  SHA256:     {result['content_sha256'][:16]}...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
