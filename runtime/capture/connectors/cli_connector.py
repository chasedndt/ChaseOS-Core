"""
cli_connector.py — ChaseOS Phase 8 Pass 3
CLI connector: captures content from stdin or a file path.

This is the reference connector — the simplest possible source. It accepts
raw text (stdin or file) and the user-provided metadata fields, then returns
a ContentPacket ready for intake_writer.

Pass 2 additions:
  - Populates original_name from the captured filename (if file-based)
  - Populates original_path_or_uri from the resolved file path (if file-based)
  - detected_mime defaults to "text/plain; charset=utf-8" for all text captures

Pass 3 additions:
  - domain_hint, project_hint, topic_hint, event_date_hint, origin_kind,
    desired_output_kind — all optional semantic breadcrumb hint parameters
  - These are stored in the ContentPacket and written to the sidecar as hints
  - They do NOT trigger SIC, change routing, or affect promotion behavior

Usage (via canonical CLI with semantic hints):
    chaseos capture file transcript.txt \\
        --class transcript \\
        --source youtube \\
        --title "Order Flow and Market Microstructure" \\
        --domain trading-systems \\
        --project chaseos \\
        --topic market-microstructure \\
        --output-kind synthesis

    # With AI-generated origin marking:
    chaseos capture file notebooklm-output.txt \\
        --class notebooklm \\
        --source notebooklm \\
        --title "DeFi Lending Synthesis" \\
        --origin-kind ai-generated \\
        --output-kind generated-idea \\
        --workspace defi-research

Usage (via backward-compat Python module path):
    python -m runtime.capture.capture \\
        --input-class transcript \\
        --source-platform youtube \\
        --title "Order Flow and Market Microstructure" \\
        --file /path/to/transcript.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..content_packet import ContentPacket


def capture_from_cli(
    *,
    input_class: str,
    source_platform: str,
    title: str,
    source_url: str | None = None,
    author: str | None = None,
    file_path: str | None = None,
    extra_metadata: dict | None = None,
    workspace_hint: str | None = None,
    # Semantic breadcrumb hints (Pass 3)
    domain_hint: str | None = None,
    project_hint: str | None = None,
    topic_hint: str | None = None,
    event_date_hint: str | None = None,
    origin_kind: str | None = None,
    desired_output_kind: str | None = None,
    capture_method: str = "cli",
) -> ContentPacket:
    """
    Build a ContentPacket from CLI-supplied text and metadata.

    Content source (in priority order):
      1. file_path — read from disk
      2. stdin — read until EOF

    When file_path is provided, populates:
      - original_name: the filename component (e.g., "transcript.txt")
      - original_path_or_uri: the resolved absolute path

    When reading from stdin, original_name and original_path_or_uri are None.

    Pass 3 semantic breadcrumb hints (all optional):
      domain_hint       — which ChaseOS domain this belongs to
      project_hint      — which project this connects to
      topic_hint        — loose subject label
      event_date_hint   — ISO 8601 date (YYYY-MM-DD) of the event/content
      origin_kind       — "human-authored" | "ai-generated" | etc.
      desired_output_kind — "synthesis" | "briefing" | "generated-idea" | etc.

    These hints are stored in the ContentPacket and written to the sidecar.
    They do NOT trigger SIC, change routing, or affect promotion behavior.

    Args:
        input_class:         One of VALID_INPUT_CLASSES.
        source_platform:     Short source identifier (e.g. "youtube", "web").
        title:               Human-readable title for the captured content.
        source_url:          URL of the original source, if applicable.
        author:              Author or creator, if known.
        file_path:           Path to a local text file. If None, reads from stdin.
        extra_metadata:      Additional provenance fields to pass through.
        workspace_hint:      Optional SIC workspace name for future ingestion.
        domain_hint:         ChaseOS domain hint (e.g. "trading-systems").
        project_hint:        Active project hint (e.g. "chaseos").
        topic_hint:          Subject label hint (e.g. "market-microstructure").
        event_date_hint:     Event date in ISO 8601 format (e.g. "2026-03-15").
        origin_kind:         Content authorship origin (e.g. "human-authored").
        desired_output_kind: Intended output type (e.g. "synthesis").
        capture_method:      Capture method tag for audit trail.

    Returns:
        A fully-populated ContentPacket (captured_at set in __post_init__).

    Raises:
        FileNotFoundError: If file_path is specified but does not exist.
        ValueError: If content is empty or metadata fields are invalid.
    """
    original_name = None
    original_path_or_uri = None

    if file_path is not None:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")
        content = p.read_text(encoding="utf-8")
        original_name = p.name
        original_path_or_uri = str(p.resolve())
    else:
        content = sys.stdin.read()

    return ContentPacket(
        content=content,
        input_class=input_class,
        source_platform=source_platform,
        title=title,
        source_url=source_url,
        author=author,
        original_name=original_name,
        original_path_or_uri=original_path_or_uri,
        workspace_hint=workspace_hint,
        domain_hint=domain_hint,
        project_hint=project_hint,
        topic_hint=topic_hint,
        event_date_hint=event_date_hint,
        origin_kind=origin_kind,
        desired_output_kind=desired_output_kind,
        extra_metadata=extra_metadata or {},
        capture_method=capture_method,
    )
