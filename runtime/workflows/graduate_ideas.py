"""
graduate_ideas.py -- ChaseOS AOR Phase 9 Pass 4

Proposal-only idea graduation workflow.

Surfaces quarantine and generated-ideas candidates, suggests promotion
destinations, and writes a review proposal to 07_LOGS/Graduation-Proposals/.
No canonical promotion occurs during execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
import re
from typing import Any


VALID_KNOWLEDGE_CLASSES = {
    "user-origin",
    "source-derived",
    "synthesized",
    "generated-ideas",
    "system-operational",
    "canonical-state",
}


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error for bounded AOR handlers."""


@dataclass
class GraduationCandidate:
    title: str
    current_location: str
    source_kind: str
    suggested_destination: str
    suggested_knowledge_class: str
    rationale: str
    operator_decision: str


def run_graduate_ideas(inputs: dict, vault_root: Path) -> dict:
    run_date = _resolve_run_date(inputs.get("date"))
    min_age_days = _coerce_int(inputs.get("min_age_days"), default=7, minimum=0)
    candidate_limit = _coerce_int(inputs.get("candidate_limit"), default=25, minimum=1)
    include_quarantine = _coerce_bool(inputs.get("include_quarantine"), default=True)
    include_generated_ideas = _coerce_bool(inputs.get("include_generated_ideas"), default=True)

    required = [
        vault_root / "06_AGENTS" / "Knowledge-Taxonomy.md",
        vault_root / "06_AGENTS" / "AI-Generated-Output-Bridge.md",
        vault_root / "03_INPUTS" / "00_QUARANTINE",
    ]
    missing = [str(path.relative_to(vault_root)).replace("\\", "/") for path in required if not path.exists()]
    if missing:
        raise WorkflowExecutionError(f"graduate_ideas required context missing: {missing}")

    candidates: list[GraduationCandidate] = []
    files_read = [
        "06_AGENTS/Knowledge-Taxonomy.md",
        "06_AGENTS/AI-Generated-Output-Bridge.md",
        "03_INPUTS/00_QUARANTINE/",
    ]

    if include_quarantine:
        candidates.extend(_scan_quarantine_candidates(vault_root, min_age_days=min_age_days))
    if include_generated_ideas:
        candidates.extend(_scan_generated_idea_candidates(vault_root))
        if (vault_root / "02_KNOWLEDGE").exists():
            files_read.append("02_KNOWLEDGE/")

    deduped = _dedupe_candidates(candidates)[:candidate_limit]

    relative_output_path = Path("07_LOGS") / "Graduation-Proposals" / f"{run_date.isoformat()}-graduate-ideas.md"
    content = _render_markdown_proposal(
        run_date=run_date,
        candidates=deduped,
        min_age_days=min_age_days,
        include_quarantine=include_quarantine,
        include_generated_ideas=include_generated_ideas,
        files_read=files_read,
    )

    return {
        "handler_status": "executed",
        "workflow_id": "graduate_ideas",
        "date": run_date.isoformat(),
        "files_read": sorted(dict.fromkeys(files_read)),
        "summary": {
            "candidate_count": len(deduped),
            "min_age_days": min_age_days,
            "include_quarantine": include_quarantine,
            "include_generated_ideas": include_generated_ideas,
        },
        "writebacks": [
            {
                "path": str(relative_output_path).replace("\\", "/"),
                "content": content,
                "content_type": "text/markdown",
            }
        ],
    }


def _resolve_run_date(raw_value: object) -> date:
    if raw_value in (None, ""):
        return date.today()
    if isinstance(raw_value, date):
        return raw_value
    try:
        return date.fromisoformat(str(raw_value))
    except ValueError as exc:
        raise WorkflowExecutionError(f"invalid date input {raw_value!r}; expected YYYY-MM-DD") from exc


def _coerce_int(raw_value: object, default: int, minimum: int) -> int:
    if raw_value in (None, ""):
        return default
    try:
        return max(int(str(raw_value)), minimum)
    except ValueError as exc:
        raise WorkflowExecutionError(f"expected integer input, got {raw_value!r}") from exc


def _coerce_bool(raw_value: object, default: bool) -> bool:
    if raw_value in (None, ""):
        return default
    if isinstance(raw_value, bool):
        return raw_value
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise WorkflowExecutionError(f"expected boolean-like input, got {raw_value!r}")


def _scan_quarantine_candidates(vault_root: Path, min_age_days: int) -> list[GraduationCandidate]:
    quarantine_root = vault_root / "03_INPUTS" / "00_QUARANTINE"
    now = datetime.now(UTC)
    results: list[GraduationCandidate] = []
    for path in sorted(p for p in quarantine_root.rglob("*") if p.is_file()):
        text = path.read_text(encoding="utf-8") if path.suffix.lower() == ".md" else ""
        frontmatter = _parse_frontmatter(text)
        age_days = (now - datetime.fromtimestamp(path.stat().st_mtime, UTC)).days
        promotion_status = str(frontmatter.get("promotion_status") or "").strip().lower()
        if age_days < min_age_days and promotion_status != "candidate":
            continue

        title = _candidate_title(path, frontmatter, text)
        domain = _candidate_domain(path, frontmatter)
        knowledge_class = _suggest_knowledge_class(frontmatter, fallback="generated-ideas")
        destination = _suggest_destination(title, domain, knowledge_class)
        rationale_bits = []
        if promotion_status == "candidate":
            rationale_bits.append("explicit promotion_status candidate")
        if age_days >= min_age_days:
            rationale_bits.append(f"aged in quarantine for {age_days} day(s)")
        if frontmatter.get("source_ref"):
            rationale_bits.append("has source_ref metadata")
        if frontmatter.get("generated_with") or frontmatter.get("generated_by"):
            rationale_bits.append("has generated-artifact metadata")
        if not rationale_bits:
            rationale_bits.append("manual review needed; metadata is sparse")

        results.append(
            GraduationCandidate(
                title=title,
                current_location=_relative(path, vault_root),
                source_kind="quarantine",
                suggested_destination=destination,
                suggested_knowledge_class=knowledge_class,
                rationale="; ".join(rationale_bits),
                operator_decision="[ ] approve  [ ] reject  [ ] defer",
            )
        )
    return results


def _scan_generated_idea_candidates(vault_root: Path) -> list[GraduationCandidate]:
    knowledge_root = vault_root / "02_KNOWLEDGE"
    if not knowledge_root.exists():
        return []

    results: list[GraduationCandidate] = []
    for path in sorted(knowledge_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(text)
        promotion_status = str(frontmatter.get("promotion_status") or "").strip().lower()
        knowledge_class = str(frontmatter.get("knowledge_class") or "").strip()
        if knowledge_class != "generated-ideas" and promotion_status != "candidate":
            continue

        title = _candidate_title(path, frontmatter, text)
        domain = _candidate_domain(path, frontmatter)
        endorsed = str(frontmatter.get("endorsement_status") or "").strip().lower()
        suggested_class = "user-origin" if endorsed.startswith("endorsed") else "generated-ideas"
        destination = _suggest_destination(title, domain, suggested_class)
        rationale_bits = []
        if endorsed.startswith("endorsed"):
            rationale_bits.append("explicitly endorsed generated idea")
        else:
            rationale_bits.append("still in generated-ideas state; operator review required before promotion")
        if promotion_status == "candidate":
            rationale_bits.append("promotion_status candidate")

        results.append(
            GraduationCandidate(
                title=title,
                current_location=_relative(path, vault_root),
                source_kind="generated-idea",
                suggested_destination=destination,
                suggested_knowledge_class=suggested_class,
                rationale="; ".join(rationale_bits),
                operator_decision="[ ] approve  [ ] reject  [ ] defer",
            )
        )
    return results


def _candidate_title(path: Path, frontmatter: dict, text: str) -> str:
    if frontmatter.get("title"):
        return str(frontmatter["title"]).strip()
    heading_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    if heading_match:
        return heading_match.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ")


def _candidate_domain(path: Path, frontmatter: dict) -> str:
    if frontmatter.get("domain"):
        return _slugify_segment(str(frontmatter["domain"]))
    if "02_KNOWLEDGE" in path.parts:
        try:
            return _slugify_segment(path.parts[path.parts.index("02_KNOWLEDGE") + 1])
        except (ValueError, IndexError):
            return "Unclassified"
    return "Unclassified"


def _suggest_knowledge_class(frontmatter: dict, fallback: str) -> str:
    existing = str(frontmatter.get("knowledge_class") or "").strip()
    if existing in VALID_KNOWLEDGE_CLASSES and existing not in {"canonical-state", "system-operational"}:
        return existing
    if frontmatter.get("source_refs"):
        return "synthesized"
    if frontmatter.get("source_ref"):
        return "source-derived"
    if frontmatter.get("generated_with") or frontmatter.get("generated_by"):
        return "generated-ideas"
    return fallback


def _suggest_destination(title: str, domain: str, knowledge_class: str) -> str:
    slug = _slugify_segment(title)
    filename = f"{slug}.md"
    if knowledge_class == "generated-ideas":
        return f"02_KNOWLEDGE/{domain}/Generated-Ideas/{filename}"
    return f"02_KNOWLEDGE/{domain}/{filename}"


def _slugify_segment(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return slug or "unclassified"


def _dedupe_candidates(candidates: list[GraduationCandidate]) -> list[GraduationCandidate]:
    deduped: list[GraduationCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.current_location.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _coerce_frontmatter_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_frontmatter_mapping(block: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("  - ") and current_list_key:
            existing = result.get(current_list_key)
            if not isinstance(existing, list):
                existing = []
                result[current_list_key] = existing
            existing.append(_coerce_frontmatter_scalar(stripped[2:].strip()))
            continue
        if line.startswith(" "):
            return {}
        if ":" not in stripped:
            return {}
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            return {}
        if value in {">", "|"}:
            return {}
        if value:
            result[key] = _coerce_frontmatter_scalar(value)
            current_list_key = None
        else:
            result[key] = []
            current_list_key = key
    return result


def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}
    data = _parse_frontmatter_mapping(parts[1])
    return data if isinstance(data, dict) else {}


def _relative(path: Path, vault_root: Path) -> str:
    return str(path.relative_to(vault_root)).replace("\\", "/")


def _render_markdown_proposal(
    run_date: date,
    candidates: list[GraduationCandidate],
    min_age_days: int,
    include_quarantine: bool,
    include_generated_ideas: bool,
    files_read: list[str],
) -> str:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lines = [
        "---",
        "type: graduation-proposal",
        "workflow: graduate_ideas",
        f"date: {run_date.isoformat()}",
        f"generated_at: {generated_at}",
        "source: aor",
        "mode: proposal-only",
        "---",
        "",
        f"# Graduate Ideas Proposal - {run_date.isoformat()}",
        "",
        "## Run Parameters",
        f"- Candidate count: {len(candidates)}",
        f"- Minimum quarantine age: {min_age_days} day(s)",
        f"- Include quarantine: {include_quarantine}",
        f"- Include generated ideas: {include_generated_ideas}",
        "",
        "## Candidates",
    ]

    if candidates:
        for candidate in candidates:
            lines.extend(
                [
                    f"### {candidate.title}",
                    f"- Current location: `{candidate.current_location}`",
                    f"- Source kind: {candidate.source_kind}",
                    f"- Suggested destination: `{candidate.suggested_destination}`",
                    f"- Suggested knowledge_class: `{candidate.suggested_knowledge_class}`",
                    f"- Rationale: {candidate.rationale}",
                    f"- Operator decision: {candidate.operator_decision}",
                    "",
                ]
            )
    else:
        lines.append("- No candidates met the current review criteria.")
        lines.append("")

    lines.extend(
        [
            "## Non-Goals",
            "- No files were promoted, moved, or rewritten by this workflow.",
            "- Any canonical write still requires an explicit operator-directed follow-up pass.",
            "",
            "## Files Read",
        ]
    )
    lines.extend([f"- `{path}`" for path in sorted(dict.fromkeys(files_read))])
    lines.append("")
    return "\n".join(lines)
