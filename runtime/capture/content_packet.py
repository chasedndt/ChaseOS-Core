"""
content_packet.py — ChaseOS Phase 8 Pass 3
Normalized content container for the Connector / Capture layer.

A ContentPacket is the output of any connector and the input to the intake writer.
It carries normalized metadata plus raw content text. It is connector-agnostic.

The intake_writer receives a ContentPacket and handles:
  - routing to 03_INPUTS/00_QUARANTINE/[class]/
  - deterministic filename generation
  - sidecar .meta.json generation (schema v8.3)
  - file write to vault

QUARANTINE — SIC HANDOFF DOCTRINE:
  ContentPackets land in 03_INPUTS/00_QUARANTINE/ as raw quarantine.
  They are NOT ingested into SIC at capture time.
  The pipeline is: capture -> quarantine -> human review -> Gate promotion -> SIC ingestion (later).
  This module does not know about, and must not trigger, SIC.

SEMANTIC BREADCRUMBS (Pass 3):
  Pass 3 adds six semantic hint fields to ContentPacket and the sidecar:
    domain_hint         — which ChaseOS domain this content belongs to
    project_hint        — which active project this might connect to
    topic_hint          — loose subject label for later SIC workspace grouping
    event_date_hint     — when the event/content occurred (not capture date)
    origin_kind         — whether the content is human-authored, ai-generated, etc.
    desired_output_kind — what kind of output the operator wants this to feed

  These are hints only. They do not cause auto-promotion, do not trigger SIC,
  and do not imply any semantic placement has occurred. They are stored in the
  sidecar metadata as breadcrumbs for future SIC workspace grouping, AOR scheduling,
  and operator review surfaces.

Design constraints:
  - ContentPacket is a pure data container. No I/O, no vault writes.
  - All fields are optional except content, input_class, source_platform, title.
  - Extra metadata that doesn't fit the standard fields goes in extra_metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime as _datetime, timezone as _tz


# ── Canonical input classes ────────────────────────────────────────────────────

INPUT_CLASS_TRANSCRIPT   = "transcript"
INPUT_CLASS_DIGEST       = "digest"
INPUT_CLASS_NOTEBOOKLM   = "notebooklm"
INPUT_CLASS_SOURCE       = "source"
INPUT_CLASS_CLIPBOARD    = "clipboard"
INPUT_CLASS_JOURNAL      = "journal"
INPUT_CLASS_YOUTUBE_NOTE = "youtube_note"

VALID_INPUT_CLASSES: frozenset[str] = frozenset({
    INPUT_CLASS_TRANSCRIPT,
    INPUT_CLASS_DIGEST,
    INPUT_CLASS_NOTEBOOKLM,
    INPUT_CLASS_SOURCE,
    INPUT_CLASS_CLIPBOARD,
    INPUT_CLASS_JOURNAL,
    INPUT_CLASS_YOUTUBE_NOTE,
})


# ── Semantic breadcrumb hint vocabularies (non-enforced, guidance only) ────────

# origin_kind — what kind of entity authored this content at its source
# These are hints; any string is accepted. Canonical values for reference:
#   "human-authored"          — article, paper, documentation, lecture by a person
#   "ai-generated"            — NotebookLM export, Perplexity synthesis, GPT output
#   "human-ai-collaborative"  — meeting transcript, annotated AI output
#   "personal-reflection"     — user's own notes, journal, memos
#   "raw-data"                — data feeds, tables, structured exports
ORIGIN_KIND_HUMAN_AUTHORED         = "human-authored"
ORIGIN_KIND_AI_GENERATED           = "ai-generated"
ORIGIN_KIND_HUMAN_AI_COLLABORATIVE = "human-ai-collaborative"
ORIGIN_KIND_PERSONAL_REFLECTION    = "personal-reflection"
ORIGIN_KIND_RAW_DATA               = "raw-data"

# desired_output_kind — what the operator intends this content to feed into
# These are hints; any string is accepted. Canonical values for reference:
#   "synthesis"      — multi-source synthesis note in 02_KNOWLEDGE/
#   "briefing"       — feeds a Scheduled Briefing Pipeline
#   "generated-idea" — AI-generated idea note (Layer C, generated-ideas knowledge class)
#   "source-note"    — promoted as a source-derived knowledge note
#   "reference"      — stored as a reference source; not for direct synthesis
DESIRED_OUTPUT_KIND_SYNTHESIS     = "synthesis"
DESIRED_OUTPUT_KIND_BRIEFING      = "briefing"
DESIRED_OUTPUT_KIND_GENERATED_IDEA = "generated-idea"
DESIRED_OUTPUT_KIND_SOURCE_NOTE   = "source-note"
DESIRED_OUTPUT_KIND_REFERENCE     = "reference"


# ── Content packet ─────────────────────────────────────────────────────────────

@dataclass
class ContentPacket:
    """
    Normalized metadata + raw content text for one capture event.

    Produced by connectors. Consumed by intake_writer.

    Core fields (required):
        content:          The raw text content to be written into quarantine.
                          Must be non-empty for a meaningful capture.
        input_class:      One of VALID_INPUT_CLASSES. Determines the destination
                          subfolder in 03_INPUTS/00_QUARANTINE/.
        source_platform:  Short identifier for where the content came from.
                          Examples: "youtube", "perplexity", "grok", "notebooklm",
                          "web", "pdf", "clipboard", "local-file", "manual".
        title:            Human-readable title for the captured content.
                          Used in filename slug generation.

    Optional provenance fields:
        source_url:       URL of the original source, if applicable.
        author:           Author or creator, if known.
        captured_at:      ISO 8601 UTC timestamp of capture.
                          Defaults to now (UTC) if not provided.
        original_name:    Original filename or resource name at source.
                          E.g., "transcript.txt", "lecture-4.pdf".
        original_path_or_uri: Full path or URI to the original resource.
                          E.g., "/home/user/downloads/transcript.txt" or a URL.
        detected_mime:    MIME type of the source content.
                          Defaults to "text/plain; charset=utf-8".
        workspace_hint:   Optional SIC workspace name for future ingestion.
                          Not used at capture time — stored for later reference.

    Semantic breadcrumb fields (Pass 3 — all optional hints):
        domain_hint:      Which ChaseOS domain this content belongs to.
                          E.g., "trading-systems", "ai-engineering", "cybersecurity".
                          Not authoritative — a hint for later SIC workspace grouping.
        project_hint:     Which active project this content might connect to.
                          E.g., "chaseos", "tradesync", "strikezone".
                          Not authoritative — a hint for later project linking.
        topic_hint:       Loose subject label within the domain.
                          E.g., "market-microstructure", "semantic-routing".
                          Not authoritative — a hint for retrieval context.
        event_date_hint:  ISO 8601 date (YYYY-MM-DD) of when the event/content
                          occurred, if different from capture date.
                          E.g., "2026-03-15" for a lecture from that date.
                          Not authoritative — for temporal organization hints only.
        origin_kind:      What kind of entity authored this content at source.
                          E.g., "human-authored", "ai-generated". See module-level
                          ORIGIN_KIND_* constants for canonical values.
        desired_output_kind: What kind of output the operator wants this to feed.
                          E.g., "synthesis", "briefing", "generated-idea". See
                          DESIRED_OUTPUT_KIND_* constants for canonical values.

    IMPORTANT: Semantic hints do not trigger SIC. They do not auto-promote content.
    They are stored in the sidecar as breadcrumbs for future human/operator use.
    All promotion remains governed and human-gated.

    Operational fields:
        extra_metadata:   Any extra provenance fields that don't fit the schema.
        capture_method:   How the content was captured ("cli", "manual", "agent",
                          "watched_folder", "api"). For audit trail only.
        injection_scan:   Whether injection scan has been performed.
                          Default "not-scanned". The user performs triage later.
    """

    # Required
    content:         str
    input_class:     str
    source_platform: str
    title:           str

    # Optional provenance
    source_url:           str | None = None
    author:               str | None = None
    captured_at:          str | None = None   # ISO 8601 UTC; defaults to now
    original_name:        str | None = None   # original filename at source
    original_path_or_uri: str | None = None   # full path or URI at source
    detected_mime:        str = "text/plain; charset=utf-8"
    workspace_hint:       str | None = None   # SIC workspace hint (future use)

    # Semantic breadcrumb hints (Pass 3)
    # These are hints only — they do not cause auto-promotion or SIC invocation.
    domain_hint:          str | None = None   # e.g. "trading-systems"
    project_hint:         str | None = None   # e.g. "chaseos"
    topic_hint:           str | None = None   # e.g. "market-microstructure"
    event_date_hint:      str | None = None   # ISO 8601 date: "YYYY-MM-DD"
    origin_kind:          str | None = None   # e.g. "human-authored", "ai-generated"
    desired_output_kind:  str | None = None   # e.g. "synthesis", "briefing"

    # Operational
    extra_metadata:  dict = field(default_factory=dict)
    capture_method:  str = "cli"
    injection_scan:  str = "not-scanned"

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("ContentPacket.content must not be empty.")
        if self.input_class not in VALID_INPUT_CLASSES:
            raise ValueError(
                f"Unknown input_class '{self.input_class}'. "
                f"Valid classes: {sorted(VALID_INPUT_CLASSES)}"
            )
        if not self.source_platform:
            raise ValueError("ContentPacket.source_platform must not be empty.")
        if not self.title:
            raise ValueError("ContentPacket.title must not be empty.")

        # Default captured_at to now (UTC)
        if not self.captured_at:
            self.captured_at = _datetime.now(_tz.utc).isoformat()

    @property
    def knowledge_class(self) -> str:
        """
        Return the ChaseOS knowledge_class appropriate for this input class.

        Raw inputs in quarantine are always 'user-origin' for journal entries
        or 'source-derived' for external research content. They are NOT canonical
        until promoted through the Gate.

        Note: if origin_kind == "ai-generated", the content may become a
        "generated-ideas" artifact after promotion — but that determination is
        made by the operator at review time, not here.
        """
        if self.input_class == INPUT_CLASS_JOURNAL:
            return "user-origin"
        return "source-derived"
