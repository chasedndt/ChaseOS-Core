"""
meeting_ingest_linker.py — ChaseOS Phase 9 Feature 14
Meeting Ingest Linker

Post-capture enrichment for meeting transcripts. Extracts entity mentions from
captured transcript text, matches them to existing vault nodes (projects, domain
knowledge files, wikilinks), applies CGL eligibility checks, and writes a
link-proposal file for operator review.

No vault content is modified. Proposals are operator-reviewed before any link
application pass.

Entity extraction sources (in order of confidence):
  1. [[wikilink]] mentions     — explicit vault references (confidence 0.9)
  2. Project name matches      — 01_PROJECTS/ directory / OS file names (0.7)
  3. Domain index matches      — 02_KNOWLEDGE/ domain keyword coverage (0.5)

CGL check:
  - For each matched target, resolve_context_eligibility is called.
  - Blocked targets appear in the proposal with a CGL note; they are not omitted.
  - Fail-open: if CGL check errors, proposal is included as eligible with a note.

Output:
  07_LOGS/Link-Proposals/YYYY-MM-DD-[capture_id]-links.md

Public API:
  run_meeting_ingest_linker(inputs, vault_root) -> dict
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from runtime.aor.context_governance import (
        read_note_cgl,
        resolve_context_eligibility,
    )
    _CGL_AVAILABLE = True
except Exception:  # noqa: BLE001
    _CGL_AVAILABLE = False


# ── Errors ─────────────────────────────────────────────────────────────────────


class WorkflowExecutionError(RuntimeError):
    pass


# ── Domain keyword coverage (subset — for domain-level proposal matching) ──────
# Each entry: (domain_letter, display_name, keywords, knowledge_subdir)
# knowledge_subdir: relative path under 02_KNOWLEDGE/ for this domain's index.

_DOMAIN_MAP: list[tuple[str, str, list[str], str]] = [
    ("A", "ChaseOS / System Infrastructure",
     ["chaseos", "claude", "runtime", "agent", "vault", "hook", "mcp", "aor", "sbp"],
     "ChaseOS"),
    ("B", "Trading Systems",
     ["trading", "trade", "position", "strategy", "backtest", "alpha", "signal", "pnl"],
     "Trading-Systems"),
    ("C", "DeFi / Crypto",
     ["defi", "crypto", "blockchain", "protocol", "liquidity", "yield", "token", "web3"],
     "DeFi"),
    ("D", "AI / Machine Learning",
     ["ai", "llm", "model", "inference", "embedding", "training", "neural", "gpt", "claude"],
     "AI-Agents"),
    ("E", "Cybersecurity",
     ["security", "cyber", "pentest", "exploit", "vulnerability", "threat", "ctf"],
     "Cybersecurity"),
    ("F", "Product / Business",
     ["product", "business", "startup", "market", "revenue", "customer", "growth"],
     "Product"),
    ("G", "Research / Knowledge",
     ["research", "knowledge", "note", "paper", "literature", "synthesis", "study"],
     "Research"),
    ("H", "Health / Wellbeing",
     ["health", "fitness", "sleep", "nutrition", "wellbeing", "workout", "recovery"],
     "Health"),
    ("I", "Identity / Principles",
     ["identity", "principle", "soul", "values", "doctrine", "philosophy", "belief"],
     "Doctrine"),
    ("J", "Finance / Wealth",
     ["finance", "wealth", "investment", "portfolio", "tax", "accounting", "budget"],
     "Finance"),
    ("K", "Legal / Compliance",
     ["legal", "compliance", "contract", "regulation", "law", "terms", "privacy"],
     "Legal"),
    ("L", "People / Relationships",
     ["people", "relationship", "team", "contact", "network", "colleague", "mentor"],
     "People"),
    ("M", "Media / Content",
     ["media", "content", "video", "podcast", "article", "writing", "publish"],
     "Media"),
    ("N", "Tools / Infrastructure",
     ["tool", "infrastructure", "server", "database", "api", "service", "devops"],
     "Tools"),
    ("O", "Education / Learning",
     ["education", "learning", "course", "skill", "study", "tutorial", "curriculum"],
     "Education"),
    ("P", "Projects / Execution",
     ["project", "execution", "milestone", "sprint", "task", "deadline", "delivery"],
     "Projects"),
    ("Q", "Creative / Design",
     ["creative", "design", "art", "visual", "aesthetic", "brand", "prototype"],
     "Creative"),
    ("R", "Operations / Admin",
     ["operations", "admin", "process", "procedure", "sop", "workflow", "policy"],
     "Operations"),
]

# Pre-build reverse domain keyword lookup: keyword → (domain_letter, display_name, subdir)
_KEYWORD_TO_DOMAIN: dict[str, tuple[str, str, str]] = {}
for _letter, _name, _kws, _subdir in _DOMAIN_MAP:
    for _kw in _kws:
        _KEYWORD_TO_DOMAIN[_kw.lower()] = (_letter, _name, _subdir)


# ── Data types ─────────────────────────────────────────────────────────────────


@dataclass
class EntityMention:
    text: str
    kind: str  # "wikilink" | "project" | "domain"
    source_line: int = 0


@dataclass
class LinkProposal:
    entity_text: str
    entity_kind: str
    target_path: str         # vault-relative path
    target_title: str
    confidence: float        # 0.0–1.0
    rationale: str
    cgl_eligible: bool = True
    cgl_note: Optional[str] = None


# ── Entity extraction ──────────────────────────────────────────────────────────


_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+?)?\]\]")


def _extract_wikilinks(text: str) -> list[EntityMention]:
    mentions: list[EntityMention] = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        for m in _WIKILINK_RE.finditer(line):
            target = m.group(1).strip()
            if target:
                mentions.append(EntityMention(text=target, kind="wikilink", source_line=lineno))
    return mentions


def _extract_project_mentions(text: str, vault_root: Path) -> list[EntityMention]:
    """Scan 01_PROJECTS/ for project names, check if any appear in transcript."""
    mentions: list[EntityMention] = []
    projects_dir = vault_root / "01_PROJECTS"
    if not projects_dir.exists():
        return mentions

    project_names: list[str] = []
    for child in projects_dir.iterdir():
        if child.is_dir():
            project_names.append(child.name)
        elif child.suffix in (".md", ".yaml") and "OS" in child.stem:
            stem = child.stem.replace("-OS", "").replace("_OS", "")
            project_names.append(stem)

    # Check each project name against transcript (word-boundary match, case-insensitive)
    text_lower = text.lower()
    lines = text.splitlines()
    for pname in project_names:
        if len(pname) < 3:
            continue
        pattern = re.compile(r"\b" + re.escape(pname.lower()) + r"\b")
        for lineno, line in enumerate(lines, 1):
            if pattern.search(line.lower()):
                mentions.append(EntityMention(text=pname, kind="project", source_line=lineno))
                break  # one mention per project is enough

    return mentions


def _extract_domain_mentions(text: str) -> list[EntityMention]:
    """Match domain keywords against transcript text."""
    mentions: list[EntityMention] = []
    seen_domains: set[str] = set()

    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        words = re.findall(r"\b\w+\b", line.lower())
        for word in words:
            if word in _KEYWORD_TO_DOMAIN:
                domain_letter, domain_name, _ = _KEYWORD_TO_DOMAIN[word]
                if domain_letter not in seen_domains:
                    seen_domains.add(domain_letter)
                    mentions.append(EntityMention(
                        text=domain_name,
                        kind="domain",
                        source_line=lineno,
                    ))

    return mentions


def extract_entities(text: str, vault_root: Path) -> list[EntityMention]:
    """Extract all entity mentions from transcript text."""
    entities: list[EntityMention] = []
    entities.extend(_extract_wikilinks(text))
    entities.extend(_extract_project_mentions(text, vault_root))
    entities.extend(_extract_domain_mentions(text))
    return entities


# ── Vault matching ─────────────────────────────────────────────────────────────


def _find_wikilink_target(name: str, vault_root: Path) -> Optional[tuple[str, str]]:
    """
    Search vault for a note matching `name`.
    Returns (vault_relative_path, display_title) or None.
    Searches: direct name.md, then recursive glob for *name*.md.
    """
    candidates: list[Path] = []

    # Direct match
    direct = vault_root / f"{name}.md"
    if direct.exists():
        candidates.append(direct)

    # Recursive search (limited depth to avoid performance issues)
    if not candidates:
        pattern = f"**/{name}.md"
        candidates = list(vault_root.glob(pattern))

    if not candidates:
        # Try case-insensitive by checking all .md files in common dirs
        for search_dir in ["01_PROJECTS", "02_KNOWLEDGE", "00_HOME"]:
            sdir = vault_root / search_dir
            if sdir.exists():
                for md in sdir.rglob("*.md"):
                    if md.stem.lower() == name.lower():
                        candidates.append(md)
                        break

    if not candidates:
        return None

    best = sorted(candidates)[0]
    rel = best.relative_to(vault_root)
    return (str(rel), best.stem)


def _find_project_target(project_name: str, vault_root: Path) -> Optional[tuple[str, str]]:
    """Find the OS file for a project name."""
    project_dir = vault_root / "01_PROJECTS" / project_name
    if project_dir.exists():
        for candidate in [
            project_dir / f"{project_name}-OS.md",
            project_dir / f"{project_name}_OS.md",
        ]:
            if candidate.exists():
                return (str(candidate.relative_to(vault_root)), candidate.stem)
        # Any OS file in the dir
        for md in project_dir.glob("*OS*.md"):
            return (str(md.relative_to(vault_root)), md.stem)
        # Fallback: project dir itself
        return (str(project_dir.relative_to(vault_root)), project_name)

    # Check for flat OS file
    for stem_variant in [f"{project_name}-OS", f"{project_name}_OS"]:
        flat = vault_root / "01_PROJECTS" / f"{stem_variant}.md"
        if flat.exists():
            return (str(flat.relative_to(vault_root)), flat.stem)

    return None


def _find_domain_target(entity_text: str, vault_root: Path) -> Optional[tuple[str, str]]:
    """Find the knowledge index file for a domain name."""
    # Reverse lookup: entity_text is the display name from _DOMAIN_MAP
    for letter, name, kws, subdir in _DOMAIN_MAP:
        if name == entity_text:
            domain_dir = vault_root / "02_KNOWLEDGE" / subdir
            if domain_dir.exists():
                # Look for the index file: subdir.md or any .md in the dir
                index = domain_dir / f"{subdir}.md"
                if index.exists():
                    return (str(index.relative_to(vault_root)), subdir)
                for md in sorted(domain_dir.glob("*.md")):
                    return (str(md.relative_to(vault_root)), md.stem)
            # Fallback: domain dir itself
            if domain_dir.exists():
                return (str(domain_dir.relative_to(vault_root)), subdir)
    return None


def match_entities_to_vault(
    entities: list[EntityMention],
    vault_root: Path,
) -> list[LinkProposal]:
    proposals: list[LinkProposal] = []
    seen_targets: set[str] = set()

    for entity in entities:
        target: Optional[tuple[str, str]] = None
        confidence: float = 0.0
        rationale: str = ""

        if entity.kind == "wikilink":
            target = _find_wikilink_target(entity.text, vault_root)
            confidence = 0.9
            rationale = f"Explicit [[wikilink]] mention at line {entity.source_line}"

        elif entity.kind == "project":
            target = _find_project_target(entity.text, vault_root)
            confidence = 0.7
            rationale = (
                f"Project name '{entity.text}' matched in transcript "
                f"(line {entity.source_line}); linked to project OS file"
            )

        elif entity.kind == "domain":
            target = _find_domain_target(entity.text, vault_root)
            confidence = 0.5
            rationale = (
                f"Domain keyword cluster '{entity.text}' detected "
                f"(line {entity.source_line}); linked to knowledge index"
            )

        if target is None:
            continue

        target_path, target_title = target
        if target_path in seen_targets:
            continue
        seen_targets.add(target_path)

        proposals.append(LinkProposal(
            entity_text=entity.text,
            entity_kind=entity.kind,
            target_path=target_path,
            target_title=target_title,
            confidence=confidence,
            rationale=rationale,
        ))

    return proposals


# ── CGL check ─────────────────────────────────────────────────────────────────


def _check_cgl(proposal: LinkProposal, vault_root: Path) -> LinkProposal:
    """Apply CGL eligibility check to a proposal's target. Fail-open."""
    if not _CGL_AVAILABLE:
        proposal.cgl_eligible = True
        proposal.cgl_note = "CGL unavailable — defaulting to eligible"
        return proposal

    try:
        target_abs = vault_root / proposal.target_path
        cgl = read_note_cgl(target_abs)
        result = resolve_context_eligibility(cgl, "read_metadata", card=None)
        if result.eligibility == "blocked":
            proposal.cgl_eligible = False
            proposal.cgl_note = f"CGL blocked: {result.reason}"
        elif result.eligibility == "restricted":
            proposal.cgl_eligible = True
            proposal.cgl_note = f"CGL restricted (metadata-only): {result.reason}"
        else:
            proposal.cgl_eligible = True
            proposal.cgl_note = None
    except Exception:  # noqa: BLE001
        proposal.cgl_eligible = True
        proposal.cgl_note = "CGL check error — defaulting to eligible"

    return proposal


def apply_cgl_checks(proposals: list[LinkProposal], vault_root: Path) -> list[LinkProposal]:
    return [_check_cgl(p, vault_root) for p in proposals]


# ── Render ─────────────────────────────────────────────────────────────────────


def _render_proposal_report(
    proposals: list[LinkProposal],
    transcript_path: str,
    capture_id: str,
    report_date: str,
    entity_count: int,
) -> str:
    eligible = [p for p in proposals if p.cgl_eligible]
    blocked = [p for p in proposals if not p.cgl_eligible]

    lines: list[str] = [
        "---",
        f"type: link-proposal",
        f"capture_id: {capture_id}",
        f"transcript: {transcript_path}",
        f"report_date: {report_date}",
        f"proposal_count: {len(proposals)}",
        f"eligible_count: {len(eligible)}",
        f"blocked_count: {len(blocked)}",
        f"entity_mentions_found: {entity_count}",
        "status: pending-operator-review",
        "---",
        "",
        f"# Meeting Ingest Link Proposal — {report_date}",
        "",
        "> **READ-ONLY PROPOSAL** — No vault content has been modified.",
        "> Review this file and run the operator apply step to accept links.",
        "",
        f"**Transcript:** `{transcript_path}`",
        f"**Capture ID:** `{capture_id}`",
        f"**Entities found:** {entity_count}",
        f"**Link proposals:** {len(proposals)} ({len(eligible)} eligible, {len(blocked)} blocked by CGL)",
        "",
    ]

    if eligible:
        lines.append("## Eligible Link Proposals")
        lines.append("")
        lines.append("| Entity | Kind | Target | Confidence | Rationale |")
        lines.append("|--------|------|--------|-----------|-----------|")
        for p in sorted(eligible, key=lambda x: -x.confidence):
            conf_pct = f"{p.confidence * 100:.0f}%"
            target_link = f"[[{p.target_title}]]"
            rationale_short = p.rationale[:80] + "..." if len(p.rationale) > 80 else p.rationale
            lines.append(
                f"| `{p.entity_text}` | {p.entity_kind} | {target_link} | {conf_pct} | {rationale_short} |"
            )
        lines.append("")

        lines.append("### Detail")
        lines.append("")
        for p in sorted(eligible, key=lambda x: -x.confidence):
            lines.append(f"#### {p.entity_text} ({p.entity_kind})")
            lines.append(f"- **Target path:** `{p.target_path}`")
            lines.append(f"- **Confidence:** {p.confidence * 100:.0f}%")
            lines.append(f"- **Rationale:** {p.rationale}")
            if p.cgl_note:
                lines.append(f"- **CGL note:** {p.cgl_note}")
            lines.append("")

    if blocked:
        lines.append("## CGL-Blocked Proposals")
        lines.append("")
        lines.append("> These targets were matched but blocked by the Context Governance Layer.")
        lines.append("> They are recorded for operator awareness only.")
        lines.append("")
        for p in blocked:
            lines.append(f"- `{p.entity_text}` → `{p.target_path}` — {p.cgl_note}")
        lines.append("")

    if not proposals:
        lines.append("## No Link Proposals")
        lines.append("")
        lines.append(
            "No vault nodes were matched to the entity mentions extracted from this transcript."
        )
        lines.append(
            "This may indicate: (1) the transcript references external people/projects not in "
            "the vault, (2) entity extraction found no matching keywords, or (3) the matching "
            "threshold was not met."
        )
        lines.append("")

    lines.append("## Operator Apply Step")
    lines.append("")
    lines.append("To apply accepted links, review the proposals above and run:")
    lines.append("```")
    lines.append(f"chaseos intake link-apply <proposal_path>")
    lines.append("```")
    lines.append("")
    lines.append("*(link-apply is a future Phase 9 operator command — not yet implemented)*")
    lines.append("")
    lines.append("---")
    lines.append("*Meeting Ingest Linker — ChaseOS Phase 9 Feature 14*")
    lines.append("*This file is a read-only proposal. No vault content was modified.*")

    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────


def run_meeting_ingest_linker(inputs: dict[str, Any], vault_root: Path) -> dict[str, Any]:
    """
    AOR handler for the meeting_ingest_linker workflow.

    Inputs:
        transcript_path  — vault-relative or absolute path to the transcript file (required)
        capture_id       — optional string ID (defaults to transcript filename stem)
        date             — optional ISO date string (defaults to today UTC)
        min_confidence   — optional float threshold (default 0.4); proposals below this are excluded

    Returns:
        writeback dict with keys: files_written, proposal_count, entity_count,
                                  eligible_count, blocked_count
    """
    # ── Parse inputs ────────────────────────────────────────────────────────────
    vault_root = vault_root.resolve()
    raw_path = inputs.get("transcript_path", "")
    if not raw_path:
        raise WorkflowExecutionError(
            "meeting_ingest_linker: 'transcript_path' input is required"
        )

    # Resolve path: try as absolute, then as vault-relative
    candidate = Path(str(raw_path))
    if not candidate.is_absolute():
        candidate = vault_root / candidate
    if not candidate.exists():
        raise WorkflowExecutionError(
            f"meeting_ingest_linker: transcript not found at '{raw_path}'"
        )

    transcript_path_abs = candidate
    transcript_path_rel = str(transcript_path_abs.relative_to(vault_root))

    capture_id = str(inputs.get("capture_id", "") or transcript_path_abs.stem)
    # Sanitise capture_id for use in filename
    capture_id_safe = re.sub(r"[^\w\-]", "_", capture_id)[:64]

    raw_date = inputs.get("date", "")
    if raw_date:
        try:
            datetime.fromisoformat(str(raw_date))
            report_date = str(raw_date)[:10]
        except ValueError:
            raise WorkflowExecutionError(
                f"meeting_ingest_linker: invalid date '{raw_date}' (expected ISO YYYY-MM-DD)"
            )
    else:
        report_date = datetime.now(timezone.utc).date().isoformat()

    try:
        min_confidence = float(inputs.get("min_confidence", 0.4))
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError()
    except (ValueError, TypeError):
        raise WorkflowExecutionError(
            "meeting_ingest_linker: 'min_confidence' must be a float 0.0–1.0"
        )

    # ── Read transcript ─────────────────────────────────────────────────────────
    try:
        transcript_text = transcript_path_abs.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise WorkflowExecutionError(
            f"meeting_ingest_linker: could not read transcript: {exc}"
        )

    # ── Extract entities ────────────────────────────────────────────────────────
    entities = extract_entities(transcript_text, vault_root)
    entity_count = len(entities)

    # ── Match to vault ──────────────────────────────────────────────────────────
    proposals = match_entities_to_vault(entities, vault_root)

    # ── Filter by confidence ────────────────────────────────────────────────────
    proposals = [p for p in proposals if p.confidence >= min_confidence]

    # ── CGL check ───────────────────────────────────────────────────────────────
    proposals = apply_cgl_checks(proposals, vault_root)

    eligible_count = sum(1 for p in proposals if p.cgl_eligible)
    blocked_count = sum(1 for p in proposals if not p.cgl_eligible)

    # ── Render ──────────────────────────────────────────────────────────────────
    report_text = _render_proposal_report(
        proposals=proposals,
        transcript_path=transcript_path_rel,
        capture_id=capture_id,
        report_date=report_date,
        entity_count=entity_count,
    )

    output_filename = f"{report_date}-{capture_id_safe}-links.md"
    output_rel = f"07_LOGS/Link-Proposals/{output_filename}"

    return {
        "handler_status": "executed",
        "workflow_id": "meeting_ingest_linker",
        "proposal_count": len(proposals),
        "entity_count": entity_count,
        "eligible_count": eligible_count,
        "blocked_count": blocked_count,
        "transcript_path": transcript_path_rel,
        "report_path": output_rel,
        "report_date": report_date,
        "writebacks": [
            {
                "path": output_rel,
                "content": report_text,
                "content_type": "text/markdown",
            }
        ],
    }
