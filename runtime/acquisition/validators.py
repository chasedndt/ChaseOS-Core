"""Validation helpers for Acquisition + Normalization Pass 1A.

The validators in this module are intentionally small and fail-closed. They
cover the first implementation substrate only: acquisition plans,
source_packet, normalized_source_pack, and briefing_ready_input_set artifacts.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .source_classes import SOURCE_CLASS_PREFIXES


SCHEMA_VERSION = "anl.v1"
OWNER_LAYER = "acquisition_normalization"

ALLOWED_SURFACES: frozenset[str] = frozenset({
    "vault_file",
    "log_file",
    "manual_drop_in",
    "quarantine_artifact",
    "captured_external",
    "browser_artifact",
    "prior_run_ref",
    "staged_capture",   # pre-captured live content written to staging before builder runs
})

ALLOWED_METHODS: frozenset[str] = frozenset({
    "direct_file_read",
    "capture_sidecar_read",
    "connector_capture_rehydration",
    "browser_artifact_read",
    "prior_run_log_read",
    "user_confirmed_import",
    "watch_folder_capture_rehydration",
    "manual_declared_ref",
})

ALLOWED_TRIGGERS: frozenset[str] = frozenset({
    "manual",
    "on_demand",
    "schedule_declared",
    "fixture",
})

ALLOWED_WRITE_TARGETS: tuple[str, ...] = (
    "runtime/acquisition/packs/",
    "runtime/acquisition/staging/",
    "07_LOGS/Acquisition-Packs/",
)

FORBIDDEN_WRITE_ZONES: tuple[str, ...] = (
    "00_HOME/",
    "01_PROJECTS/",
    "02_KNOWLEDGE/",
    "03_INPUTS/",
    "06_AGENTS/",
    "README.md",
    "PROJECT_FOUNDATION.md",
    "ROADMAP.md",
    "SOUL.md",
    "CLAUDE.md",
    "runtime/mcp/",
)

READABLE_SUFFIXES: frozenset[str] = frozenset({
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".html",
})

CREDENTIAL_MARKERS: tuple[str, ...] = (
    ".env",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
)

class AcquisitionValidationError(ValueError):
    """Raised when a Pass 1A acquisition object violates the contract."""


def as_mapping(value: Any) -> dict[str, Any]:
    """Return a plain dict for dataclasses or mappings."""
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise AcquisitionValidationError(f"expected mapping, got {type(value).__name__}")


def normalize_relative_path(path: str) -> str:
    """Normalize a repo-relative path to forward slashes."""
    return str(Path(str(path).strip())).replace("\\", "/")


def reject_credential_markers(value: str, field_name: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in CREDENTIAL_MARKERS):
        raise AcquisitionValidationError(f"{field_name} looks credential-related and is blocked: {value!r}")


def validate_relative_path(path_raw: str, field_name: str) -> str:
    """Validate a path is repo-relative, non-escaping, and non-credential-like."""
    path = str(path_raw or "").strip().replace("\\", "/")
    if not path:
        raise AcquisitionValidationError(f"{field_name} is required")
    if path.startswith("/") or Path(path).is_absolute():
        raise AcquisitionValidationError(f"{field_name} must be relative to the vault root")
    if ".." in [part for part in path.split("/") if part]:
        raise AcquisitionValidationError(f"{field_name} may not leave the vault root")
    reject_credential_markers(path, field_name)
    return path


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_source_path_for_class(path: str, source_class: str) -> None:
    """Enforce conservative first-wave source class path prefixes."""
    prefixes = SOURCE_CLASS_PREFIXES.get(source_class)
    if not prefixes:
        raise AcquisitionValidationError(f"unsupported source_class: {source_class!r}")
    if not any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes):
        raise AcquisitionValidationError(
            f"source_class {source_class!r} may not read path {path!r}"
        )


def validate_output_root(path_raw: str, field_name: str = "output_targets.pack_root") -> str:
    """Validate a write root is bounded to acquisition packet/log targets."""
    path = validate_relative_path(path_raw, field_name).rstrip("/")
    for forbidden in FORBIDDEN_WRITE_ZONES:
        forbidden_norm = forbidden.rstrip("/")
        if path == forbidden_norm or path.startswith(forbidden_norm + "/"):
            raise AcquisitionValidationError(f"{field_name} is in forbidden zone {forbidden!r}: {path!r}")
    if not any(path == target.rstrip("/") or path.startswith(target) for target in ALLOWED_WRITE_TARGETS):
        raise AcquisitionValidationError(
            f"{field_name} is outside allowed acquisition packet targets: {path!r}"
        )
    return path


def validate_write_path(path_raw: str) -> str:
    path = validate_relative_path(path_raw, "writeback.path")
    parent = str(Path(path).parent).replace("\\", "/")
    validate_output_root(parent, "writeback.parent")
    return path


def validate_network_scope(network_scope: Any) -> list[Any]:
    """Pass 1A permits no live network acquisition."""
    if network_scope in (None, "", []):
        return []
    if isinstance(network_scope, list) and not network_scope:
        return []
    raise AcquisitionValidationError("network_scope must be empty in Pass 1A")


def validate_browser_scope(browser_scope: Any) -> list[Any]:
    """Validate declared browser scope without granting live browser authority."""
    if browser_scope in (None, ""):
        return []
    entries = browser_scope if isinstance(browser_scope, list) else [browser_scope]
    validated = []
    for entry in entries:
        if isinstance(entry, Mapping):
            if entry.get("allow_forms") or entry.get("allow_downloads") or entry.get("allow_credentials"):
                raise AcquisitionValidationError(
                    "browser_scope may not allow forms, downloads, or credentials in Pass 1A"
                )
            value = str(entry.get("origin") or entry.get("url") or "").strip()
            validated.append(dict(entry))
        else:
            value = str(entry or "").strip()
            validated.append(value)
        if not value or "*" in value:
            raise AcquisitionValidationError("browser_scope must name a concrete http(s) origin or URL")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AcquisitionValidationError("browser_scope must be http(s) with a concrete host")
        if parsed.username or parsed.password:
            raise AcquisitionValidationError("browser_scope may not include credentials")
    return validated


def require_keys(data: Mapping[str, Any], keys: set[str], object_name: str) -> None:
    missing = [key for key in sorted(keys) if key not in data]
    if missing:
        raise AcquisitionValidationError(f"{object_name} missing required keys: {missing}")


def ensure_no_canonical_mutation(promotion: Mapping[str, Any], object_name: str) -> None:
    if bool(promotion.get("canonical_mutation_allowed")):
        raise AcquisitionValidationError(f"{object_name} may not allow canonical mutation in Pass 1A")


def validate_artifact_envelope(data: Mapping[str, Any], artifact_type: str) -> None:
    require_keys(
        data,
        {
            "artifact_id",
            "artifact_type",
            "schema_version",
            "created_at",
            "owner_layer",
            "owning_workflow",
            "objective",
            "acquirer",
            "scope",
            "promotion",
            "audit",
        },
        artifact_type,
    )
    if data["artifact_type"] != artifact_type:
        raise AcquisitionValidationError(f"expected artifact_type {artifact_type!r}, got {data['artifact_type']!r}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise AcquisitionValidationError(f"{artifact_type} schema_version must be {SCHEMA_VERSION!r}")
    if data["owner_layer"] != OWNER_LAYER:
        raise AcquisitionValidationError(f"{artifact_type} owner_layer must be {OWNER_LAYER!r}")
    ensure_no_canonical_mutation(as_mapping(data["promotion"]), artifact_type)


def validate_source_packet(packet_raw: Any) -> dict[str, Any]:
    packet = as_mapping(packet_raw)
    validate_artifact_envelope(packet, "source_packet")
    require_keys(
        packet,
        {
            "source_class",
            "source_origin",
            "acquisition_method",
            "provenance",
            "trust_evaluation",
            "freshness",
            "transformation_chain",
            "content_sha256",
            "raw_pointer",
            "normalized_text",
        },
        "source_packet",
    )
    require_keys(as_mapping(packet["source_origin"]), {"kind", "ref", "display_name"}, "source_packet.source_origin")
    require_keys(
        as_mapping(packet["provenance"]),
        {
            "source_origin",
            "acquisition_method",
            "acquirer",
            "captured_at",
            "content_sha256",
            "raw_pointer",
            "representation_level",
        },
        "source_packet.provenance",
    )
    require_keys(
        as_mapping(packet["trust_evaluation"]),
        {
            "base_trust_tier",
            "assigned_by",
            "confidence",
            "quality_marker",
            "operator_approval_state",
            "actionability",
        },
        "source_packet.trust_evaluation",
    )
    require_keys(
        as_mapping(packet["freshness"]),
        {"captured_at", "freshness_window", "staleness_policy", "time_sensitive_domain"},
        "source_packet.freshness",
    )
    if not isinstance(packet["transformation_chain"], list) or not packet["transformation_chain"]:
        raise AcquisitionValidationError("source_packet.transformation_chain must be a non-empty list")
    return packet


def validate_normalized_source_pack(pack_raw: Any) -> dict[str, Any]:
    pack = as_mapping(pack_raw)
    validate_artifact_envelope(pack, "normalized_source_pack")
    require_keys(
        pack,
        {
            "items",
            "source_packet_refs",
            "source_packet_count",
            "trust_summary",
            "freshness_summary",
            "transformation_chain",
        },
        "normalized_source_pack",
    )
    if int(pack["source_packet_count"]) != len(pack["source_packet_refs"]):
        raise AcquisitionValidationError("normalized_source_pack source_packet_count mismatch")
    return pack


def validate_briefing_ready_input_set(briefing_raw: Any) -> dict[str, Any]:
    briefing = as_mapping(briefing_raw)
    validate_artifact_envelope(briefing, "briefing_ready_input_set")
    require_keys(
        briefing,
        {
            "normalized_source_pack_ref",
            "sections",
            "trust_summary",
            "freshness_summary",
            "actionability",
            "source_refs",
            "transformation_chain",
        },
        "briefing_ready_input_set",
    )
    actionability = as_mapping(briefing["actionability"])
    if actionability.get("allowed_use") != "briefing_only":
        raise AcquisitionValidationError("briefing_ready_input_set allowed_use must be briefing_only in Pass 1A")
    blocked = set(actionability.get("blocked_actions") or [])
    if "canonical_knowledge_promotion" not in blocked:
        raise AcquisitionValidationError("briefing_ready_input_set must block canonical knowledge promotion")
    return briefing
