"""Acquisition source-class registry.

Source classes describe what a source is. Surfaces and methods describe how it
arrived. Keep this registry generic: source-class identity must not imply new
browser, MCP, API, delivery, or canonical-write authority.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceClassDefinition:
    source_class: str
    family: str
    default_surface: str
    default_origin_kind: str
    allowed_path_prefixes: tuple[str, ...]


_LOCAL_RESEARCH_PREFIXES = (
    "runtime/acquisition/fixtures/",
    "runtime/acquisition/manual/",
    "runtime/acquisition/staging/",
    "03_INPUTS/00_QUARANTINE/",
)


SOURCE_CLASS_DEFINITIONS: dict[str, SourceClassDefinition] = {
    "vault_note": SourceClassDefinition(
        source_class="vault_note",
        family="internal_context",
        default_surface="vault_file",
        default_origin_kind="vault",
        allowed_path_prefixes=("00_HOME/", "02_KNOWLEDGE/", "04_SOPS/", "05_TEMPLATES/", "06_AGENTS/"),
    ),
    "project_note": SourceClassDefinition(
        source_class="project_note",
        family="internal_context",
        default_surface="vault_file",
        default_origin_kind="vault",
        allowed_path_prefixes=("01_PROJECTS/",),
    ),
    "build_log": SourceClassDefinition(
        source_class="build_log",
        family="runtime_history",
        default_surface="log_file",
        default_origin_kind="runtime_log",
        allowed_path_prefixes=("07_LOGS/Build-Logs/",),
    ),
    "operator_log": SourceClassDefinition(
        source_class="operator_log",
        family="runtime_history",
        default_surface="log_file",
        default_origin_kind="runtime_log",
        allowed_path_prefixes=("07_LOGS/Operator-Briefs/", "07_LOGS/Agent-Activity/", "07_LOGS/SBP-Runs/"),
    ),
    "quarantine_digest": SourceClassDefinition(
        source_class="quarantine_digest",
        family="external_digest",
        default_surface="quarantine_artifact",
        default_origin_kind="quarantine",
        allowed_path_prefixes=("03_INPUTS/00_QUARANTINE/",),
    ),
    "captured_external": SourceClassDefinition(
        source_class="captured_external",
        family="external_capture",
        default_surface="quarantine_artifact",
        default_origin_kind="quarantine",
        allowed_path_prefixes=("03_INPUTS/00_QUARANTINE/",),
    ),
    "visual_capture": SourceClassDefinition(
        source_class="visual_capture",
        family="visual_capture_markdown",
        default_surface="quarantine_artifact",
        default_origin_kind="quarantine",
        allowed_path_prefixes=("03_INPUTS/00_QUARANTINE/",),
    ),
    "browser_artifact": SourceClassDefinition(
        source_class="browser_artifact",
        family="browser_capture",
        default_surface="browser_artifact",
        default_origin_kind="browser",
        allowed_path_prefixes=("03_INPUTS/00_QUARANTINE/", "07_LOGS/Operator-Screenshots/", "runtime/acquisition/fixtures/"),
    ),
    "prior_run_ref": SourceClassDefinition(
        source_class="prior_run_ref",
        family="runtime_history",
        default_surface="prior_run_ref",
        default_origin_kind="runtime_log",
        allowed_path_prefixes=("07_LOGS/", "99_ARCHIVE/Documentation-History/"),
    ),
    "manual_drop_in": SourceClassDefinition(
        source_class="manual_drop_in",
        family="manual_import",
        default_surface="manual_drop_in",
        default_origin_kind="manual_import",
        allowed_path_prefixes=("runtime/acquisition/fixtures/", "runtime/acquisition/manual/", "03_INPUTS/00_QUARANTINE/"),
    ),
    "staged_capture": SourceClassDefinition(
        source_class="staged_capture",
        family="staged_capture",
        default_surface="staged_capture",
        default_origin_kind="ai-generated",
        allowed_path_prefixes=("runtime/acquisition/staging/",),
    ),
    "perplexity_digest": SourceClassDefinition(
        source_class="perplexity_digest",
        family="trading_research_digest",
        default_surface="manual_drop_in",
        default_origin_kind="ai-generated",
        allowed_path_prefixes=_LOCAL_RESEARCH_PREFIXES,
    ),
    "youtube_summary": SourceClassDefinition(
        source_class="youtube_summary",
        family="trading_research_summary",
        default_surface="manual_drop_in",
        default_origin_kind="manual_import",
        allowed_path_prefixes=_LOCAL_RESEARCH_PREFIXES,
    ),
    "research_export": SourceClassDefinition(
        source_class="research_export",
        family="trading_research_export",
        default_surface="manual_drop_in",
        default_origin_kind="manual_import",
        allowed_path_prefixes=_LOCAL_RESEARCH_PREFIXES,
    ),
    "grok_digest": SourceClassDefinition(
        source_class="grok_digest",
        family="trading_research_digest",
        default_surface="manual_drop_in",
        default_origin_kind="ai-generated",
        allowed_path_prefixes=_LOCAL_RESEARCH_PREFIXES,
    ),
}

# Optionally extend with instance-specific source classes (monorepo only; absent in MIT
# Core, which keeps the registry generic — no instance/markets taxonomy leak).
try:
    from runtime.acquisition.source_classes_instance import INSTANCE_SOURCE_CLASS_DEFINITIONS as _INSTANCE_DEFS
    SOURCE_CLASS_DEFINITIONS.update(_INSTANCE_DEFS)
except ImportError:
    pass


ALLOWED_SOURCE_CLASSES: frozenset[str] = frozenset(SOURCE_CLASS_DEFINITIONS)
TRADING_RESEARCH_SOURCE_CLASSES: frozenset[str] = frozenset({
    "perplexity_digest",
    "youtube_summary",
    "research_export",
    "grok_digest",
})
# The instance/markets thesis source-class set lives in the monorepo-only
# source_classes_instance pack — not re-exported here, so Core stays free of instance
# taxonomy. Instance code imports that set directly from the instance pack.
SOURCE_CLASS_PREFIXES: dict[str, tuple[str, ...]] = {
    key: value.allowed_path_prefixes for key, value in SOURCE_CLASS_DEFINITIONS.items()
}


_PLATFORM_SOURCE_CLASS: dict[str, str] = {
    "perplexity": "perplexity_digest",
    "grok": "grok_digest",
    "youtube": "youtube_summary",
    "youtube_summary": "youtube_summary",
    "research_export": "research_export",
}


def get_source_class_definition(source_class: str) -> SourceClassDefinition:
    try:
        return SOURCE_CLASS_DEFINITIONS[source_class]
    except KeyError as exc:
        raise ValueError(f"unknown acquisition source_class: {source_class!r}") from exc


def default_surface_for_source_class(source_class: str) -> str:
    return get_source_class_definition(source_class).default_surface


def default_origin_kind_for_source_class(source_class: str) -> str:
    return get_source_class_definition(source_class).default_origin_kind


def source_class_for_platform(source_platform: str) -> str:
    return _PLATFORM_SOURCE_CLASS.get(str(source_platform or "").strip().lower(), "staged_capture")
