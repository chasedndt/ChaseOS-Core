"""
input_adapters.py — SBP Input Adapter Layer (Phase 9 Pass 1B)

Provides the generic InputAdapter protocol and concrete/stub implementations.
Each adapter collects input data from a declared source and returns it with a
trust tier assigned per ChaseOS Trust-Tiers.md:

  Tier 1 — canonical vault state (Now.md, project OS files)
  Tier 2 — trusted internal records (build logs, audit logs)
  Tier 3 — research and workspace outputs (SIC workspaces, digests)
  Tier 4 — external, untrusted content (external APIs, web clips)

Tier 4 inputs are NEVER treated as instructions per AOR Principle 3
(Prompt-Injection Hardening).

Concrete implementations:
  VaultNotesInputAdapter — reads declared vault files; trust tier 1
  AcquisitionPackInputAdapter — reads briefing_ready_input_set from
    runtime/acquisition/ packs; trust tier declared in manifest (default 2)

Stub implementations (Pass 1B+ targets):
  SICWorkspaceInputAdapterStub, ExternalAPIInputAdapterStub,
  AgentActivityInputAdapterStub, RawDigestInputAdapterStub

Public API:
    get_input_adapter(adapter_type) -> InputAdapter
    InputAdapter (ABC)
    InputAdapterError
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from .manifest import SBPInputAdapterConfig


class InputAdapterError(RuntimeError):
    """Raised when input collection fails in a non-recoverable way."""


class InputAdapter(ABC):
    """Abstract base for SBP input adapters.

    Subclasses implement collect() to pull data from a specific source type
    and return it with a declared trust tier.
    """
    adapter_type: str = ""
    default_trust_tier: int = 3

    @abstractmethod
    def collect(self, config: SBPInputAdapterConfig, vault_root: Path) -> dict:
        """Collect input data from the declared source.

        Returns dict with:
          content: str | None — collected text content
          trust_tier: int — trust level of this input
          sources: list[str] — paths or references read
          stub: bool — True if this is a stub implementation
        """
        ...


class VaultNotesInputAdapter(InputAdapter):
    """Reads declared vault notes/files. Trust tier 1 (canonical vault state).

    Reads files declared in config.paths relative to vault_root.
    If a path points to a directory, reads all .md/.txt/.yaml files within it.
    Missing declared paths raise InputAdapterError (fail-closed).
    """
    adapter_type = "vault-notes"
    default_trust_tier = 1

    def collect(self, config: SBPInputAdapterConfig, vault_root: Path) -> dict:
        trust_tier = config.trust_tier if config.trust_tier else self.default_trust_tier
        sources: list[str] = []
        sections: list[str] = []

        for rel_path in config.paths:
            full_path = vault_root / rel_path
            if not full_path.exists():
                raise InputAdapterError(
                    f"VaultNotesInputAdapter: declared path '{rel_path}' does not exist "
                    f"at vault root '{vault_root}'"
                )
            if full_path.is_file():
                text = full_path.read_text(encoding="utf-8")
                sections.append(f"# {rel_path}\n\n{text}")
                sources.append(rel_path)
            elif full_path.is_dir():
                for f in sorted(full_path.iterdir()):
                    if f.is_file() and f.suffix in {".md", ".txt", ".yaml"}:
                        text = f.read_text(encoding="utf-8")
                        rel_f = str(f.relative_to(vault_root)).replace("\\", "/")
                        sections.append(f"# {rel_f}\n\n{text}")
                        sources.append(rel_f)

        return {
            "content": "\n\n---\n\n".join(sections) if sections else None,
            "trust_tier": trust_tier,
            "sources": sources,
            "stub": False,
        }


class AcquisitionPackInputAdapter(InputAdapter):
    """Reads a briefing_ready_input_set artifact produced by runtime/acquisition/.

    Locates the BRIS JSON at config.pack_path (relative to vault_root), then
    reads all source_packet_*.json files in the same directory to extract
    normalized text and provenance metadata.

    Trust tier is taken from the manifest config (default 2 — internal runtime artifact).
    If config.optional is True and the pack is missing, returns a stub result instead
    of raising InputAdapterError, allowing the SBP pipeline to degrade gracefully.

    This is a generic SBP adapter. Any SBP pipeline that has an acquisition-pack
    upstream can declare this adapter type.
    """
    adapter_type = "acquisition-pack"
    default_trust_tier = 2

    _MAX_PACKET_CHARS = 2000  # per-packet text ceiling for briefing context

    def _resolve_pack_path(self, config: SBPInputAdapterConfig, vault_root: Path) -> str | None:
        """Resolve the BRIS path. Pointer file takes precedence over static pack_path.

        When pack_latest_path is set, reads the pointer JSON to find the most recently
        written briefing_ready_input_set. Falls back to static pack_path if pointer is
        absent or unreadable (so acquisition outage doesn't permanently break the adapter).
        """
        if config.pack_latest_path:
            pointer_file = vault_root / config.pack_latest_path
            if pointer_file.exists():
                try:
                    pointer = json.loads(pointer_file.read_text(encoding="utf-8"))
                    bris_path = pointer.get("briefing_ready_input_set_path")
                    if bris_path and isinstance(bris_path, str):
                        return bris_path
                except (json.JSONDecodeError, OSError):
                    pass  # pointer unreadable — fall through to static pack_path
        return config.pack_path or None

    def collect(self, config: SBPInputAdapterConfig, vault_root: Path) -> dict:
        pack_path_rel = self._resolve_pack_path(config, vault_root)
        if not pack_path_rel:
            if config.optional:
                return {
                    "content": None,
                    "trust_tier": config.trust_tier or self.default_trust_tier,
                    "sources": [],
                    "stub": True,
                    "stub_reason": (
                        "acquisition pack path could not be resolved (optional=true — "
                        "pipeline proceeds without acquisition sources)"
                    ),
                }
            raise InputAdapterError(
                "AcquisitionPackInputAdapter: 'pack_path' or 'pack_latest_path' is required in adapter config"
            )

        pack_path = vault_root / pack_path_rel
        if not pack_path.exists():
            if config.optional:
                return {
                    "content": None,
                    "trust_tier": config.trust_tier or self.default_trust_tier,
                    "sources": [],
                    "stub": True,
                    "stub_reason": (
                        f"acquisition pack not found at '{pack_path_rel}' (optional=true — "
                        "pipeline proceeds without acquisition sources)"
                    ),
                }
            raise InputAdapterError(
                f"AcquisitionPackInputAdapter: briefing_ready_input_set not found "
                f"at '{pack_path_rel}' — ensure acquisition ran before this pipeline"
            )

        # Read and validate the BRIS
        try:
            bris = json.loads(pack_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputAdapterError(
                f"AcquisitionPackInputAdapter: malformed JSON at '{pack_path_rel}': {exc}"
            ) from exc

        if not isinstance(bris, dict):
            raise InputAdapterError(
                f"AcquisitionPackInputAdapter: '{pack_path_rel}' must be a JSON object"
            )
        if bris.get("artifact_type") != "briefing_ready_input_set":
            raise InputAdapterError(
                f"AcquisitionPackInputAdapter: '{pack_path_rel}' artifact_type is "
                f"'{bris.get('artifact_type')}', expected 'briefing_ready_input_set'"
            )

        # Read source packets from the same directory (sorted by filename)
        pack_dir = pack_path.parent
        source_packets: list[dict] = []
        packet_paths: list[str] = []
        for sp_file in sorted(pack_dir.glob("source_packet_*.json")):
            try:
                sp = json.loads(sp_file.read_text(encoding="utf-8"))
                if isinstance(sp, dict):
                    source_packets.append(sp)
                    packet_paths.append(
                        str(sp_file.relative_to(vault_root)).replace("\\", "/")
                    )
            except (json.JSONDecodeError, OSError):
                pass  # skip unreadable packets; BRIS metadata still available

        # Build formatted content from normalized text
        sections: list[str] = []
        for packet in source_packets:
            display = packet.get("source_origin", {}).get("display_name", "unknown source")
            trust = packet.get("trust_evaluation", {}).get("base_trust_tier", "?")
            freshness = packet.get("freshness", {}).get("freshness_window", "unknown")
            text = str(packet.get("normalized_text") or "").strip()
            if len(text) > self._MAX_PACKET_CHARS:
                text = text[: self._MAX_PACKET_CHARS] + "\n*(truncated)*"
            sections.append(
                f"### {display} (trust tier {trust}, freshness: {freshness})\n\n{text}"
            )

        trust_tier = config.trust_tier if config.trust_tier else self.default_trust_tier
        return {
            "content": "\n\n".join(sections) if sections else None,
            "trust_tier": trust_tier,
            "sources": [pack_path_rel] + packet_paths,
            "stub": False,
            "trust_summary": bris.get("trust_summary", {}),
            "freshness_summary": bris.get("freshness_summary", {}),
            "blocked_actions": bris.get("actionability", {}).get("blocked_actions", []),
            "artifact_id": bris.get("artifact_id", ""),
        }


class SICWorkspaceInputAdapterStub(InputAdapter):
    """Stub — SIC workspace query input adapter. Not yet implemented in Pass 1A.

    Full implementation is a Pass 1B target. SIC workspace queries are trust tier 3
    (research output, not canonical state).
    """
    adapter_type = "sic-workspace"
    default_trust_tier = 3

    def collect(self, config: SBPInputAdapterConfig, vault_root: Path) -> dict:
        return {
            "content": None,
            "trust_tier": self.default_trust_tier,
            "sources": [],
            "stub": True,
            "stub_reason": "SIC workspace input adapter not yet implemented (SBP Pass 1B target)",
        }


class ExternalAPIInputAdapterStub(InputAdapter):
    """Stub — external API input adapter. Trust tier 4 (untrusted). Not yet implemented.

    Tier 4 content is NEVER treated as instructions per AOR Principle 3.
    Credential handling for external APIs follows Credential-Boundaries-SOP.md.
    """
    adapter_type = "external-api"
    default_trust_tier = 4

    def collect(self, config: SBPInputAdapterConfig, vault_root: Path) -> dict:
        return {
            "content": None,
            "trust_tier": self.default_trust_tier,
            "sources": [],
            "stub": True,
            "stub_reason": (
                "external-api input adapter not yet implemented; "
                "Tier 4 content is never treated as instructions per AOR Principle 3"
            ),
        }


class AgentActivityInputAdapterStub(InputAdapter):
    """Stub — agent activity log reader. Trust tier 2. Not yet implemented in Pass 1A."""
    adapter_type = "agent-activity"
    default_trust_tier = 2

    def collect(self, config: SBPInputAdapterConfig, vault_root: Path) -> dict:
        return {
            "content": None,
            "trust_tier": self.default_trust_tier,
            "sources": [],
            "stub": True,
            "stub_reason": "agent-activity input adapter not yet implemented (SBP Pass 1B target)",
        }


class RawDigestInputAdapterStub(InputAdapter):
    """Stub — raw digest reader (03_INPUTS/Digests/). Trust tier 3. Not yet implemented."""
    adapter_type = "raw-digest"
    default_trust_tier = 3

    def collect(self, config: SBPInputAdapterConfig, vault_root: Path) -> dict:
        return {
            "content": None,
            "trust_tier": self.default_trust_tier,
            "sources": [],
            "stub": True,
            "stub_reason": "raw-digest input adapter not yet implemented (SBP Pass 1B target)",
        }


_ADAPTER_REGISTRY: dict[str, type[InputAdapter]] = {
    "vault-notes": VaultNotesInputAdapter,
    "acquisition-pack": AcquisitionPackInputAdapter,
    "sic-workspace": SICWorkspaceInputAdapterStub,
    "external-api": ExternalAPIInputAdapterStub,
    "agent-activity": AgentActivityInputAdapterStub,
    "raw-digest": RawDigestInputAdapterStub,
}


def get_input_adapter(adapter_type: str) -> InputAdapter:
    """Factory: return InputAdapter instance for the given adapter type."""
    cls = _ADAPTER_REGISTRY.get(adapter_type)
    if cls is None:
        raise InputAdapterError(
            f"unknown input adapter type '{adapter_type}'; "
            f"registered types: {sorted(_ADAPTER_REGISTRY)}"
        )
    return cls()
