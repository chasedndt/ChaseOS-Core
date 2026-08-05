"""
developer_shadow.py -- ChaseOS AOR Phase 9

Developer Co-Development Mode — shadow/draft-only workflow handler.

Feature identity: ChaseOS-owned. Not Claude-owned. Not harness-owned.
This handler runs through adapters. It does not belong to any adapter.

Public API:
    run_developer_repo_explain(inputs, vault_root) -> dict

Inputs:
    focus_area      -- path or subsystem name (e.g. "runtime/aor", "Phase 9")
    question        -- what the developer wants to understand
    target_paths    -- list or comma-separated paths to read explicitly
    project_scope   -- project context (e.g. "ChaseOS Phase 9")

Outputs (all draft-only — no canonical writes):
    draft_developer_brief_path
    draft_contradiction_scan_path
    draft_doc_refresh_proposal_path
    draft_implementation_brief_path
    draft_diagram_proposal_path
    build_log_path
    archive_note_path

Write scope: Developer-Briefs, Agent-Activity, Build-Logs, and Documentation-History only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FORBIDDEN_WRITE_ZONES = [
    "SOUL.md",
    "CLAUDE.md",
    "00_HOME/Principles.md",
    "00_HOME/Operating-System.md",
    "00_HOME/Assistant-Contract.md",
    "00_HOME/Now.md",
    "README.md",
    "PROJECT_FOUNDATION.md",
    "ROADMAP.md",
    "FORKING.md",
    "06_AGENTS/Agent-Control-Plane.md",
    "06_AGENTS/Permission-Matrix.md",
    "06_AGENTS/Trust-Tiers.md",
    "06_AGENTS/Handoff-Protocol.md",
    "01_PROJECTS/",
    "02_KNOWLEDGE/",
    "03_INPUTS/",
    "runtime/",
]

ALLOWED_WRITE_TARGETS = [
    "07_LOGS/Developer-Briefs/",
    "07_LOGS/Agent-Activity/",
    "07_LOGS/Build-Logs/",
    "99_ARCHIVE/Documentation-History/",
]

_READABLE_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".toml", ".txt"}
_MAX_FILES_PER_DIR = 20
_BROAD_READ_TARGETS = {
    ".",
    "./",
    "*",
    "**",
    "/",
    "00_HOME",
    "01_PROJECTS",
    "02_KNOWLEDGE",
    "03_INPUTS",
    "04_SOPS",
    "05_TEMPLATES",
    "06_AGENTS",
    "07_LOGS",
    "99_ARCHIVE",
    "runtime",
}
_CREDENTIAL_PATH_MARKERS = (
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


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error for bounded AOR handlers."""


@dataclass
class ContradictionFinding:
    file_path: str
    finding_type: str  # "phase_mismatch", "stale_claim", "version_drift"
    description: str
    confidence: str    # "high", "medium", "low"


# ── Input resolution ──────────────────────────────────────────────────────────

def _coerce_path_list(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return [str(p).strip() for p in raw if str(p).strip()]


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _assert_declared_read_scope_safe(raw_path: str, vault_root: Path, field_name: str) -> None:
    """Reject root-level, path-escape, and credential-like read targets."""
    normalized = str(raw_path or "").strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return

    lowered = normalized.lower()
    parts = [part for part in lowered.split("/") if part]
    broad_targets = {target.lower() for target in _BROAD_READ_TARGETS}

    if normalized in _BROAD_READ_TARGETS or lowered in broad_targets:
        raise WorkflowExecutionError(
            f"developer_repo_explain: {field_name}={raw_path!r} is too broad for declared read scope"
        )

    if ".." in parts:
        raise WorkflowExecutionError(
            f"developer_repo_explain: {field_name}={raw_path!r} attempts to leave the vault root"
        )

    if any(marker in lowered for marker in _CREDENTIAL_PATH_MARKERS):
        raise WorkflowExecutionError(
            f"developer_repo_explain: {field_name}={raw_path!r} looks credential-related and is blocked"
        )

    candidate = (vault_root / normalized).resolve()
    root = vault_root.resolve()
    if candidate.exists() and not _path_is_relative_to(candidate, root):
        raise WorkflowExecutionError(
            f"developer_repo_explain: {field_name}={raw_path!r} resolves outside the vault root"
        )


def _resolve_focus_paths(focus_area: str, target_paths: list[str], vault_root: Path) -> list[Path]:
    """
    Resolve the declared narrow read scope.

    Always includes CLAUDE.md (routing anchor).
    Adds explicit target_paths files/dirs.
    Adds focus_area path if it resolves to a real path.
    No ambient traversal beyond what is declared.
    """
    paths: list[Path] = []

    claude_md = vault_root / "CLAUDE.md"
    if claude_md.exists():
        paths.append(claude_md)

    for tp in target_paths:
        _assert_declared_read_scope_safe(tp, vault_root, "target_paths")
        full = vault_root / tp
        if full.exists():
            if full.is_file() and full.suffix in _READABLE_SUFFIXES:
                paths.append(full)
            elif full.is_dir():
                for f in sorted(full.iterdir()):
                    if f.is_file() and f.suffix in _READABLE_SUFFIXES:
                        paths.append(f)
                    if len(paths) >= _MAX_FILES_PER_DIR * 2:
                        break

    if focus_area:
        _assert_declared_read_scope_safe(focus_area, vault_root, "focus_area")
        focus_path = vault_root / focus_area
        if focus_path.exists() and focus_path not in paths:
            if focus_path.is_file() and focus_path.suffix in _READABLE_SUFFIXES:
                paths.append(focus_path)
            elif focus_path.is_dir():
                for f in sorted(focus_path.iterdir()):
                    if f.is_file() and f.suffix in _READABLE_SUFFIXES:
                        if f not in paths:
                            paths.append(f)
                    if len(paths) >= _MAX_FILES_PER_DIR * 3:
                        break

    seen: set[str] = set()
    result: list[Path] = []
    for p in paths:
        pk = str(p)
        if pk not in seen:
            seen.add(pk)
            result.append(p)
    return result


def _read_file_safe(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


# ── Contradiction scan ────────────────────────────────────────────────────────

_PHASE_RE = re.compile(r"Phase\s+(\d+)", re.IGNORECASE)
_VERSION_RE = re.compile(r"version:\s*[\"']?(\d+\.\d+)[\"']?", re.IGNORECASE)
_NOT_BUILT_RE = re.compile(r"NOT BUILT.*?(Phase\s+\d+)", re.IGNORECASE)
_STATUS_RE = re.compile(r"status:\s*(\w+)", re.IGNORECASE)


def _scan_contradictions(
    read_files: list[tuple[str, str]],
    focus_area: str,
) -> list[ContradictionFinding]:
    """
    Heuristic contradiction scanner.

    Scans declared read files for:
    - Wide phase number spread in a single doc (possible drift)
    - Stale NOT BUILT claims referencing completed phases
    - Version number inconsistencies

    Does NOT do ambient vault traversal.
    Capped at 20 findings to stay bounded.
    """
    findings: list[ContradictionFinding] = []

    for rel_path, content in read_files:
        if not content:
            continue

        # Phase spread check
        phase_matches = _PHASE_RE.findall(content)
        numeric = {int(p) for p in phase_matches if p.isdigit()}
        if len(numeric) >= 2:
            spread = max(numeric) - min(numeric)
            if spread > 4 and rel_path.endswith(".md"):
                findings.append(ContradictionFinding(
                    file_path=rel_path,
                    finding_type="phase_mismatch",
                    description=(
                        f"Wide phase reference spread in single doc: "
                        f"phases {sorted(numeric)} (span={spread}). "
                        f"Verify all phase references are current."
                    ),
                    confidence="low",
                ))

        # Stale NOT BUILT claims in completed phases
        not_built_hits = _NOT_BUILT_RE.findall(content)
        for phase_ref in not_built_hits[:3]:
            try:
                phase_num = int(re.search(r"\d+", phase_ref).group())
            except (AttributeError, ValueError):
                continue
            if phase_num <= 8:
                findings.append(ContradictionFinding(
                    file_path=rel_path,
                    finding_type="stale_claim",
                    description=(
                        f"NOT BUILT claim references {phase_ref}. "
                        f"Phases 1-8 are complete — verify this claim is still accurate."
                    ),
                    confidence="medium",
                ))

        # Multiple conflicting status: values in same YAML file
        if rel_path.endswith((".yaml", ".yml")):
            status_vals = set(_STATUS_RE.findall(content))
            if len(status_vals) > 3:
                findings.append(ContradictionFinding(
                    file_path=rel_path,
                    finding_type="version_drift",
                    description=(
                        f"Multiple status values in single YAML: {sorted(status_vals)}. "
                        f"Verify only one is the canonical current status."
                    ),
                    confidence="low",
                ))

        if len(findings) >= 20:
            break

    return findings[:20]


# ── Output builders ───────────────────────────────────────────────────────────

def _build_developer_brief(
    focus_area: str,
    question: str,
    project_scope: str,
    read_files: list[tuple[str, str]],
    contradictions: list[ContradictionFinding],
    run_ts: str,
) -> str:
    lines = [
        f"# Developer Brief — {focus_area or 'General'}",
        "",
        f"**Generated:** {run_ts}",
        f"**Focus Area:** `{focus_area or '(not specified)'}`",
        f"**Question:** {question or '(not specified)'}",
        f"**Project Scope:** {project_scope or '(not specified)'}",
        "**Mode:** shadow/draft-only — no canonical writes",
        "",
        "---",
        "",
        "## [CONTEXT READ]",
        "",
        "Files read for this brief:",
        "",
    ]

    for rel_path, content in read_files:
        size = len(content) if content else 0
        lines.append(f"- `{rel_path}` ({size:,} chars)")

    lines += [
        "",
        "---",
        "",
        f"## [REPO TRUTH — {focus_area or 'General'}]",
        "",
    ]

    if read_files:
        for rel_path, content in read_files[:6]:
            if not content:
                continue
            summary = [l for l in content.split("\n") if l.strip()][:4]
            lines.append(f"### `{rel_path}`")
            lines.append("")
            for sl in summary:
                lines.append(f"  {sl}")
            lines.append("")
    else:
        lines += [
            f"No readable files found for focus area: `{focus_area}`",
            "",
        ]

    lines += [
        "---",
        "",
        "## [CONTRADICTION SCAN]",
        "",
    ]

    if contradictions:
        lines.append(f"**{len(contradictions)} potential issue(s) detected:**")
        lines.append("")
        for c in contradictions:
            lines.append(f"- **{c.finding_type}** ({c.confidence}) — `{c.file_path}`")
            lines.append(f"  {c.description}")
            lines.append("")
    else:
        lines += [
            "No obvious contradictions detected in declared read scope.",
            "",
        ]

    lines += [
        "---",
        "",
        "## [HOW IT FITS]",
        "",
        f"Focus area `{focus_area or '(general)'}` is part of the ChaseOS Phase 9 Operator Runtime.",
        "",
        "Read order for this area:",
        "1. `CLAUDE.md` — routing anchor and audit notes",
        "2. `00_HOME/Now.md` — current sprint focus",
        "3. Relevant Project-OS file for the domain",
        "4. The specific files in `target_paths` or `focus_area`",
        "",
        "---",
        "",
        "## [NEXT PASS SUGGESTIONS]",
        "",
        "1. Review contradiction scan findings above",
        "2. Check stale NOT BUILT claims in docs vs current repo state",
        "3. Use the Implementation Brief (separate artifact) as input to the next engineering pass",
        "4. No canonical writes have been made — all outputs are advisory",
        "",
        "---",
        "",
        "*Developer Co-Development Mode — ChaseOS Phase 9*",
        "*ChaseOS-owned feature. Runs through adapters. Does not belong to adapters.*",
    ]

    return "\n".join(lines)


def _build_contradiction_scan(
    focus_area: str,
    read_files: list[tuple[str, str]],
    contradictions: list[ContradictionFinding],
    run_ts: str,
) -> str:
    lines = [
        f"# Contradiction Scan — {focus_area or 'General'}",
        "",
        f"**Generated:** {run_ts}",
        f"**Files scanned:** {len(read_files)}",
        f"**Findings:** {len(contradictions)}",
        "**Mode:** heuristic scan — declared read scope only; no ambient traversal",
        "",
        "---",
        "",
    ]

    if contradictions:
        lines.append("## Findings")
        lines.append("")
        for i, c in enumerate(contradictions, 1):
            lines += [
                f"### Finding {i}: {c.finding_type}",
                "",
                f"**File:** `{c.file_path}`",
                f"**Confidence:** {c.confidence}",
                "",
                f"{c.description}",
                "",
                "**Action:** Verify this claim against current repo state before the next pass.",
                "",
            ]
    else:
        lines += [
            "## No Findings",
            "",
            "No obvious contradictions detected in declared read scope.",
            "",
            "Note: this is a heuristic scan only. Manual review of the focus area is always recommended.",
            "",
        ]

    lines += [
        "---",
        "",
        "*Contradiction Scan — Developer Co-Development Mode — ChaseOS Phase 9*",
    ]

    return "\n".join(lines)


def _build_implementation_brief(
    focus_area: str,
    question: str,
    project_scope: str,
    read_files: list[tuple[str, str]],
    run_ts: str,
) -> str:
    lines = [
        f"# Implementation Brief — {focus_area or 'Next Pass'}",
        "",
        f"**Date:** {run_ts}",
        f"**Focus:** `{focus_area}`",
        f"**Scope:** {project_scope}",
        "**Status:** draft — review before use as pass prompt",
        "",
        "---",
        "",
        "## Context to Read",
        "",
        "Read these files before starting the pass:",
        "",
    ]

    for rel_path, _ in read_files:
        lines.append(f"- `{rel_path}`")

    lines += [
        "",
        "---",
        "",
        "## Task",
        "",
        f"{question or '(fill in the specific task for this pass)'}",
        "",
        "---",
        "",
        "## Constraints",
        "",
        f"- Focus area: `{focus_area}`",
        f"- Project scope: `{project_scope}`",
        "- Do not modify protected files without explicit instruction",
        "- Write outputs to appropriate log / archive targets",
        "- Create build log + archive note at session close",
        "- Follow CLAUDE.md writeback requirements",
        "",
        "---",
        "",
        "## Start Sequence",
        "",
        "1. Read CLAUDE.md (routing anchor)",
        "2. Read 00_HOME/Now.md (sprint focus)",
        "3. Read the files listed in 'Context to Read' above",
        "4. Execute the task",
        "5. Write build log to 07_LOGS/Build-Logs/",
        "6. Update Now.md if phase state changed",
        "",
        "---",
        "",
        "*Generated by Developer Co-Development Mode — ChaseOS Phase 9*",
        "*This is a draft artifact. Review and adjust before executing as a pass prompt.*",
    ]

    return "\n".join(lines)


def _build_doc_refresh_proposal(
    focus_area: str,
    read_files: list[tuple[str, str]],
    contradictions: list[ContradictionFinding],
    run_ts: str,
) -> str:
    lines = [
        f"# Doc Refresh Proposal - {focus_area or 'General'}",
        "",
        f"**Generated:** {run_ts}",
        "**Status:** draft proposal only - review before any canonical edit",
        "**Read Scope:** declared files only; no ambient traversal",
        "",
        "---",
        "",
        "## Structured Diff Proposals",
        "",
    ]

    if contradictions:
        for index, finding in enumerate(contradictions, 1):
            proposal_id = f"dev-doc-refresh-{index:02d}"
            lines += [
                f"### Proposal {index}",
                "",
                f"- proposal_id: `{proposal_id}`",
                f"- target_file: `{finding.file_path}`",
                "- status: REVIEW_REQUIRED",
                "- operation: verify_then_edit",
                f"- confidence: {finding.confidence}",
                f"- finding_type: {finding.finding_type}",
                f"- reason: {finding.description}",
                "",
                "```diff",
                "# Draft-only proposal. No canonical edit has been applied.",
                f"# Verify current repo truth before changing {finding.file_path}.",
                "- <stale-or-conflicting claim>",
                "+ <truthful replacement after verification>",
                "```",
                "",
            ]
    else:
        lines += [
            "- proposal_id: `dev-doc-refresh-00`",
            "- target_file: `(none)`",
            "- status: NO_CHANGE_PROPOSED",
            "- operation: none",
            "- reason: No direct contradiction-driven doc refresh is proposed from this declared scope.",
            "",
            "If the operator wants a canonical edit, route it through a separate explicit docs pass.",
        ]

    lines += [
        "",
        "---",
        "",
        "## Files Considered",
        "",
    ]

    for rel_path, _ in read_files:
        lines.append(f"- `{rel_path}`")

    lines += [
        "",
        "---",
        "",
        "## Guardrails",
        "",
        "- This artifact does not modify README, architecture docs, project files, or knowledge notes.",
        "- It is not a promotion into `02_KNOWLEDGE/`.",
        "- Adapter-specific differences belong in adapter manifests and configs, not in feature identity.",
        "",
        "*Doc Refresh Proposal - Developer Co-Development Mode - ChaseOS Phase 9*",
    ]

    return "\n".join(lines)


def _build_run_build_log(
    focus_area: str,
    question: str,
    project_scope: str,
    read_files: list[tuple[str, str]],
    contradictions: list[ContradictionFinding],
    output_paths: list[str],
    run_ts: str,
) -> str:
    lines = [
        "# Developer Co-Development Mode Shadow Run",
        "",
        f"**Generated:** {run_ts}",
        "**Workflow:** `developer_repo_explain_shadow`",
        "**Feature Owner:** ChaseOS",
        "**Mode:** shadow / draft-only",
        f"**Focus Area:** `{focus_area or '(not specified)'}`",
        f"**Question:** {question or '(not specified)'}",
        f"**Project Scope:** {project_scope or '(not specified)'}",
        "",
        "---",
        "",
        "## What Ran",
        "",
        "The workflow read narrow declared context and generated draft developer-support artifacts.",
        "It did not perform shell, git, browser automation, network calls, credential reads, or canonical writeback.",
        "",
        "## Read Scope",
        "",
    ]

    for rel_path, _ in read_files:
        lines.append(f"- `{rel_path}`")

    lines += [
        "",
        "## Outputs",
        "",
    ]

    for path in output_paths:
        lines.append(f"- `{path}`")

    lines += [
        "",
        "## Drift Findings",
        "",
        f"- Findings detected: {len(contradictions)}",
        "",
        "## Boundary Confirmation",
        "",
        "- ChaseOS owns the feature identity.",
        "- The workflow runs through declared adapters; it is not adapter-owned.",
        "- Adapter-specific config remains outside the feature identity.",
        "- Writeback is limited to draft/log/archive targets declared by the manifest and role card.",
        "",
        "*Generated build log artifact for a shadow workflow run.*",
    ]

    return "\n".join(lines)


def _build_archive_note(
    focus_area: str,
    output_paths: list[str],
    run_ts: str,
) -> str:
    lines = [
        "# Developer Co-Development Mode Shadow Run Archive Note",
        "",
        f"**Generated:** {run_ts}",
        "**Workflow:** `developer_repo_explain_shadow`",
        f"**Focus Area:** `{focus_area or '(not specified)'}`",
        "**Status:** archive note for draft-only workflow output",
        "",
        "---",
        "",
        "## Summary",
        "",
        "Developer Co-Development Mode produced draft repo-intelligence artifacts for the declared focus area.",
        "The run preserved the ChaseOS-owned, adapter-capable boundary and made no canonical knowledge changes.",
        "",
        "## Produced Artifacts",
        "",
    ]

    for path in output_paths:
        lines.append(f"- `{path}`")

    lines += [
        "",
        "## Non-Goals Preserved",
        "",
        "- No canonical writeback",
        "- No shell, git, browser automation, network, connector, or credential access",
        "- No promotion into `02_KNOWLEDGE/`",
        "- No feature ownership transfer to any provider, harness, or adapter",
        "",
        "*Documentation-history note for Developer Co-Development Mode shadow run.*",
    ]

    return "\n".join(lines)


def _build_diagram_proposal(
    focus_area: str,
    read_files: list[tuple[str, str]],
    run_ts: str,
) -> str:
    import_re = re.compile(r"^(?:from|import)\s+([\w.]+)", re.MULTILINE)

    lines = [
        f"# Diagram Proposal — {focus_area or 'General'}",
        "",
        f"**Generated:** {run_ts}",
        "**Status:** draft text artifact — review before rendering",
        "**Note:** text/Mermaid only; no live rendering in Phase 9",
        "",
        "---",
        "",
        "## File Map (ASCII)",
        "",
        "```",
        f"{focus_area or 'focus_area'}/",
    ]

    for rel_path, _ in read_files[:12]:
        depth = rel_path.count("/")
        indent = "  " * min(depth, 3)
        lines.append(f"{indent}├── {Path(rel_path).name}")

    lines += [
        "```",
        "",
        "---",
        "",
        "## Python Import Graph (draft, from declared files)",
        "",
        "```",
    ]

    for rel_path, content in read_files:
        if not rel_path.endswith(".py") or not content:
            continue
        imports = import_re.findall(content)[:5]
        if imports:
            lines.append(f"{Path(rel_path).name}")
            for imp in imports:
                lines.append(f"  └─ {imp}")

    lines += [
        "```",
        "",
        "---",
        "",
        "## Mermaid Flow (draft)",
        "",
        "```mermaid",
        "graph TD",
        f'    Focus["{focus_area or "Focus Area"}"]',
    ]

    for i, (rel_path, _) in enumerate(read_files[:8]):
        name = Path(rel_path).name.replace(".", "_").replace("-", "_")
        lines.append(f'    F{i}["{Path(rel_path).name}"]')
        lines.append(f"    Focus --> F{i}")

    lines += [
        "```",
        "",
        "---",
        "",
        "*Diagram Proposal — Developer Co-Development Mode — ChaseOS Phase 9*",
        "*Text-only draft. Requires human review before use. Studio rendering is Phase 10.*",
    ]

    return "\n".join(lines)


# ── Write-scope validation ────────────────────────────────────────────────────

def _assert_write_path_safe(path: str) -> None:
    """Raise WorkflowExecutionError if path is outside allowed write scope."""
    for forbidden in FORBIDDEN_WRITE_ZONES:
        norm_forbidden = forbidden.rstrip("/")
        norm_path = path.replace("\\", "/")
        if norm_path == norm_forbidden or norm_path.startswith(norm_forbidden + "/"):
            raise WorkflowExecutionError(
                f"developer_shadow: write path {path!r} is in forbidden zone {forbidden!r}"
            )
    in_allowed = any(
        path.replace("\\", "/").startswith(t.rstrip("/"))
        for t in ALLOWED_WRITE_TARGETS
    )
    if not in_allowed:
        raise WorkflowExecutionError(
            f"developer_shadow: write path {path!r} is outside allowed write targets {ALLOWED_WRITE_TARGETS}"
        )


# ── Public handler ────────────────────────────────────────────────────────────

def run_developer_repo_explain(inputs: dict, vault_root: Path) -> dict:
    """
    developer_repo_explain_shadow workflow handler.

    Shadow/draft-only: reads declared narrow context, produces draft artifacts only.
    No canonical vault state is modified.

    Adapter-capable design: this handler is adapter-agnostic.
    ChaseOS-owned feature — not Claude-owned, not harness-owned.
    """
    focus_area = str(inputs.get("focus_area") or "").strip()
    question = str(inputs.get("question") or "").strip()
    target_paths = _coerce_path_list(inputs.get("target_paths"))
    project_scope = str(inputs.get("project_scope") or "").strip()

    if not focus_area and not question and not target_paths:
        raise WorkflowExecutionError(
            "developer_repo_explain requires at least one of: focus_area, question, or target_paths"
        )

    resolved_paths = _resolve_focus_paths(focus_area, target_paths, vault_root)
    if not resolved_paths:
        raise WorkflowExecutionError(
            f"developer_repo_explain: no readable files found for "
            f"focus_area={focus_area!r}, target_paths={target_paths!r}"
        )

    read_files: list[tuple[str, str]] = []
    for p in resolved_paths:
        content = _read_file_safe(p)
        if content is not None:
            rel = str(p.relative_to(vault_root)).replace("\\", "/")
            read_files.append((rel, content))

    if not read_files:
        raise WorkflowExecutionError(
            "developer_repo_explain: could not read any files in declared scope"
        )

    now_dt = datetime.now(timezone.utc)
    run_ts = now_dt.strftime("%Y-%m-%d %H:%M UTC")
    date_slug = now_dt.strftime("%Y-%m-%d")
    area_slug = re.sub(r"[^a-z0-9]+", "-", (focus_area or "general").lower())[:40].strip("-")

    contradictions = _scan_contradictions(read_files, focus_area)

    developer_brief = _build_developer_brief(
        focus_area, question, project_scope, read_files, contradictions, run_ts
    )
    contradiction_scan = _build_contradiction_scan(
        focus_area, read_files, contradictions, run_ts
    )
    doc_refresh_proposal = _build_doc_refresh_proposal(
        focus_area, read_files, contradictions, run_ts
    )
    implementation_brief = _build_implementation_brief(
        focus_area, question, project_scope, read_files, run_ts
    )
    diagram_proposal = _build_diagram_proposal(focus_area, read_files, run_ts)

    audit_ts = now_dt.strftime("%Y%m%d-%H%M%S")
    time_slug = now_dt.strftime("%H%M%S")
    brief_path = f"07_LOGS/Developer-Briefs/{date_slug}-{time_slug}-{area_slug}-developer-brief.md"
    scan_path = f"07_LOGS/Developer-Briefs/{date_slug}-{time_slug}-{area_slug}-contradiction-scan.md"
    doc_refresh_path = f"07_LOGS/Developer-Briefs/{date_slug}-{time_slug}-{area_slug}-doc-refresh-proposal.md"
    impl_path = f"07_LOGS/Developer-Briefs/{date_slug}-{time_slug}-{area_slug}-implementation-brief.md"
    diagram_path = f"07_LOGS/Developer-Briefs/{date_slug}-{time_slug}-{area_slug}-diagram-proposal.md"
    build_log_path = f"07_LOGS/Build-Logs/{date_slug}-developer-co-development-shadow-run-{time_slug}.md"
    archive_note_path = f"99_ARCHIVE/Documentation-History/{date_slug}_developer-co-development-shadow-run-{time_slug}.md"
    audit_path = f"07_LOGS/Agent-Activity/{audit_ts}__developer_repo_explain_shadow__audit.json"

    draft_output_paths = [brief_path, scan_path, doc_refresh_path, impl_path, diagram_path]
    build_log = _build_run_build_log(
        focus_area,
        question,
        project_scope,
        read_files,
        contradictions,
        draft_output_paths + [audit_path, build_log_path, archive_note_path],
        run_ts,
    )
    archive_note = _build_archive_note(
        focus_area,
        draft_output_paths + [audit_path, build_log_path],
        run_ts,
    )

    all_write_paths = [
        brief_path,
        scan_path,
        doc_refresh_path,
        impl_path,
        diagram_path,
        audit_path,
        build_log_path,
        archive_note_path,
    ]
    for wp in all_write_paths:
        _assert_write_path_safe(wp)

    audit_payload = json.dumps({
        "workflow": "developer_repo_explain_shadow",
        "timestamp": run_ts,
        "focus_area": focus_area,
        "question": question,
        "project_scope": project_scope,
        "target_paths": target_paths,
        "files_read": [r for r, _ in read_files],
        "contradictions_found": len(contradictions),
        "outputs": [
            brief_path,
            scan_path,
            doc_refresh_path,
            impl_path,
            diagram_path,
            build_log_path,
            archive_note_path,
        ],
        "mode": "shadow/draft-only",
        "adapter_note": "ChaseOS-owned feature; not adapter-owned",
    }, indent=2)

    return {
        "focus_area": focus_area,
        "question": question,
        "files_read": [r for r, _ in read_files],
        "contradictions_found": len(contradictions),
        "draft_developer_brief_path": brief_path,
        "draft_contradiction_scan_path": scan_path,
        "draft_doc_refresh_proposal_path": doc_refresh_path,
        "draft_implementation_brief_path": impl_path,
        "draft_diagram_proposal_path": diagram_path,
        "build_log_path": build_log_path,
        "archive_note_path": archive_note_path,
        "writebacks": [
            {"path": brief_path, "content": developer_brief},
            {"path": scan_path, "content": contradiction_scan},
            {"path": doc_refresh_path, "content": doc_refresh_proposal},
            {"path": impl_path, "content": implementation_brief},
            {"path": diagram_path, "content": diagram_proposal},
            {"path": audit_path, "content": audit_payload},
            {"path": build_log_path, "content": build_log},
            {"path": archive_note_path, "content": archive_note},
        ],
    }
