"""
intake_writer.py — ChaseOS Phase 8 Pass 3
Intake file writer for the Connector / Capture layer.

Receives a ContentPacket and writes two files to the quarantine boundary:
  1. The content file: deterministically named .md file with raw content
  2. The sidecar:      [filename].meta.json — canonical provenance record (schema v8.3)

Physical write target:
    03_INPUTS/00_QUARANTINE/[class_subfolder]/[YYYYMMDD-HHMMSS__class__source__slug].md
    03_INPUTS/00_QUARANTINE/[class_subfolder]/[YYYYMMDD-HHMMSS__class__source__slug].meta.json

QUARANTINE — SIC HANDOFF DOCTRINE:
  All files written here are in quarantine. They are NOT ingested into SIC at write time.
  The pipeline after this module is:
    1. Quarantine (this module writes here)
    2. Human triage review (operator reads 03_INPUTS/00_QUARANTINE/)
    3. Gate promotion (CHASEOS_PROMOTION_APPROVED=1, governed by chaseos_gate.py)
    4. SIC ingestion (runtime/source_intelligence/ — triggered separately, after promotion)
  This module does not touch the Gate, SIC, or any post-quarantine layer.

AI-GENERATED OUTPUT BRIDGE:
  If a captured item has origin_kind="ai-generated" or desired_output_kind="generated-idea",
  these are breadcrumbs only. The AI-generated output bridge is defined in:
    06_AGENTS/AI-Generated-Output-Bridge.md
  Semantic hints are stored in the sidecar. They do not change routing or promotion behavior.
  The capture layer treats AI-originated captures identically to human-authored captures —
  both land in quarantine, both require human review, both require explicit Gate promotion.

Sidecar schema version: 8.3 (Phase 8, Pass 3)
  New vs 8.2: domain_hint, project_hint, topic_hint, event_date_hint,
              origin_kind, desired_output_kind

Design constraints:
  - intake_writer performs vault I/O. It does not generate metadata.
  - All naming decisions are delegated to router.py.
  - The Gate governs promotion out of quarantine. This module does not touch it.
  - intake_writer does not promote files. It only writes quarantine files.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from .content_packet import ContentPacket
from .router import route_input_class, get_route_reason, make_filename, resolve_unique_path


# ── Public API ─────────────────────────────────────────────────────────────────

def write_intake(packet: ContentPacket, vault_root: Path) -> dict:
    """
    Write a ContentPacket to the quarantine boundary.

    Creates:
        <vault>/03_INPUTS/00_QUARANTINE/<class>/<filename>.md        — raw content file
        <vault>/03_INPUTS/00_QUARANTINE/<class>/<filename>.meta.json — sidecar (v8.3)

    Args:
        packet:     The normalized ContentPacket to write.
        vault_root: Absolute path to the vault root directory.

    Returns:
        A result dict with keys:
            content_path:   str — absolute path to the written content file
            sidecar_path:   str — absolute path to the written sidecar file
            filename:       str — the base filename (no directory)
            capture_id:     str — UUID4 assigned to this capture event
            content_sha256: str — SHA-256 hex digest of content
            quarantine_dir: str — the quarantine directory path
    """
    # Resolve destination directory (inside 00_QUARANTINE/)
    target_dir = route_input_class(packet.input_class, vault_root)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename and resolve uniqueness
    filename = make_filename(
        input_class=packet.input_class,
        source_platform=packet.source_platform,
        title=packet.title,
        captured_at=packet.captured_at,
    )
    content_path = resolve_unique_path(target_dir, filename)

    # Derive actual filename after collision resolution (may have _2, _3 suffix)
    actual_filename = content_path.name
    sidecar_path = content_path.with_suffix(".meta.json")

    # Provenance fields
    capture_id = str(uuid.uuid4())
    content_sha256 = hashlib.sha256(packet.content.encode("utf-8")).hexdigest()
    route_reason = get_route_reason(packet.input_class)

    # Write content file (raw text only — no frontmatter in quarantine)
    content_path.write_text(packet.content, encoding="utf-8")

    # Build and write sidecar (schema v8.3)
    sidecar = _build_sidecar(
        packet=packet,
        capture_id=capture_id,
        content_sha256=content_sha256,
        content_filename=actual_filename,
        route_reason=route_reason,
    )
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "content_path":   str(content_path),
        "sidecar_path":   str(sidecar_path),
        "filename":       actual_filename,
        "capture_id":     capture_id,
        "content_sha256": content_sha256,
        "quarantine_dir": str(target_dir),
    }


# ── Internal helpers ───────────────────────────────────────────────────────────

def _build_sidecar(
    packet: ContentPacket,
    capture_id: str,
    content_sha256: str,
    content_filename: str,
    route_reason: str,
) -> dict:
    """
    Build the canonical provenance sidecar dict (schema v8.3).

    The sidecar is the authoritative metadata record for a quarantine file.
    It carries all fields from the ContentPacket plus write-time provenance.

    Schema v8.3 additions vs v8.2 (Pass 3 — semantic breadcrumbs):
        domain_hint         — which ChaseOS domain this content belongs to
        project_hint        — which active project this might connect to
        topic_hint          — loose subject label for later SIC workspace grouping
        event_date_hint     — ISO 8601 date of when the event/content occurred
        origin_kind         — what kind of entity authored this (human/AI/etc.)
        desired_output_kind — what kind of output the operator wants this to feed

    SEMANTIC BREADCRUMB NOTE:
        These fields are operator-supplied hints. They do not trigger SIC.
        They do not change routing, promotion criteria, or knowledge classification.
        They are stored here so future SIC workspace grouping, AOR scheduling,
        and operator review tools can use them as context — without requiring
        the operator to re-supply them later.

    Schema v8.2 fields (Pass 2):
        original_name        — original filename at source (if applicable)
        original_path_or_uri — full path or URI at source (if applicable)
        detected_mime        — MIME type of the source content
        route_reason         — human-readable explanation of routing decision
        quarantine_status    — current operator-facing state ("pending-review")
        workspace_hint       — optional SIC workspace hint for future ingestion
        source_package_status — SIC ingestion state ("not-ingested" at capture time)

    Backward compat note:
        Consumers reading v8.1/v8.2 sidecars will not find v8.3 fields.
        Code reading sidecars should use .get() with appropriate defaults.

    SIC handoff field:
        source_package_status: "not-ingested" — set by capture layer.
        This field is a seam for future SIC ingestion tracking. SIC may update it
        to "ingested" after source package creation (post-promotion, not here).

    Gate-facing field:
        promotion_status: "quarantine" — this is what chaseos_gate.py reads.
        Never set to anything other than "quarantine" by this module.

    AI-generated output bridge:
        If origin_kind="ai-generated" or desired_output_kind="generated-idea",
        these are breadcrumbs. See 06_AGENTS/AI-Generated-Output-Bridge.md for
        the full distinction between raw capture, SIC workspace-local outputs,
        durable generated artifacts, and canonical promoted knowledge.
    """
    return {
        "schema_version":   "8.3",
        "capture_id":       capture_id,
        "content_filename": content_filename,
        "content_sha256":   content_sha256,

        # ContentPacket core fields
        "input_class":      packet.input_class,
        "source_platform":  packet.source_platform,
        "title":            packet.title,
        "captured_at":      packet.captured_at,
        "capture_method":   packet.capture_method,

        # Optional provenance
        "source_url":             packet.source_url,
        "author":                 packet.author,
        "original_name":          packet.original_name,
        "original_path_or_uri":   packet.original_path_or_uri,
        "detected_mime":          packet.detected_mime,

        # Routing metadata
        "route_reason":    route_reason,

        # Downstream routing hints
        "knowledge_class":  packet.knowledge_class,
        "injection_scan":   packet.injection_scan,

        # Operator-facing quarantine state
        "quarantine_status":     "pending-review",   # operator reads this

        # Gate-facing promotion state — managed by Gate, never changed here
        "promotion_status":      "quarantine",        # Gate reads this

        # SIC handoff state — set here, may be updated by SIC after promotion
        # SIC ingestion is NOT triggered at capture time. This is a seam only.
        "source_package_status": "not-ingested",

        # Optional SIC workspace hint — stored for future reference, not used now
        "workspace_hint":        packet.workspace_hint,

        # Semantic breadcrumb hints (Pass 3)
        # These are hints only. They do not trigger SIC, change routing,
        # or alter promotion behavior. See AI-Generated-Output-Bridge.md.
        "domain_hint":           packet.domain_hint,
        "project_hint":          packet.project_hint,
        "topic_hint":            packet.topic_hint,
        "event_date_hint":       packet.event_date_hint,
        "origin_kind":           packet.origin_kind,
        "desired_output_kind":   packet.desired_output_kind,

        # Extra connector-specific metadata passthrough
        "extra_metadata":   packet.extra_metadata,
    }
