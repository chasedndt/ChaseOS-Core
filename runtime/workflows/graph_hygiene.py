"""
graph_hygiene.py -- ChaseOS AOR Phase 9 Pass 4

Proposal-only vault graph hygiene workflow.

Scans declared vault surfaces, reports structural issues, and writes a hygiene
report to 07_LOGS/Hygiene-Reports/. No canonical vault content is modified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable, Any


WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
DEFAULT_SCAN_SCOPE = [
    "README.md",
    "PROJECT_FOUNDATION.md",
    "ROADMAP.md",
    "00_HOME/",
    "01_PROJECTS/",
    "02_KNOWLEDGE/",
    "04_SOPS/",
    "05_TEMPLATES/",
    "06_AGENTS/",
]
INDEX_FILENAMES = {"Index.md", "README.md", "Knowledge-Index.md"}


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error for bounded AOR handlers."""


@dataclass
class BrokenLink:
    source_path: str
    target: str


@dataclass
class StaleFrontmatter:
    path: str
    issues: list[str]


@dataclass
class IndexDrift:
    index_path: str
    missing_entries: list[str]


@dataclass
class AgingCapture:
    path: str
    days_old: int


def run_graph_hygiene(inputs: dict, vault_root: Path) -> dict:
    run_date = _resolve_run_date(inputs.get("date"))
    quarantine_age_days = _coerce_int(inputs.get("quarantine_age_days"), default=14, minimum=1)
    max_entries = _coerce_int(inputs.get("max_entries"), default=25, minimum=1)
    scan_scope = _resolve_scan_scope(inputs.get("scan_scope"))

    knowledge_taxonomy = vault_root / "06_AGENTS" / "Knowledge-Taxonomy.md"
    vault_map = vault_root / "06_AGENTS" / "Vault-Map.md"
    required = [knowledge_taxonomy, vault_map]
    missing = [str(path.relative_to(vault_root)).replace("\\", "/") for path in required if not path.exists()]
    if missing:
        raise WorkflowExecutionError(f"graph_hygiene required context missing: {missing}")

    markdown_files = _collect_markdown_files(vault_root, scan_scope)
    if not markdown_files:
        raise WorkflowExecutionError("graph_hygiene found no markdown files in the declared scan scope")

    files_by_stem = _build_stem_index(markdown_files, vault_root)
    outbound_map: dict[str, set[str]] = {}
    inbound_counts: dict[str, int] = {}
    broken_links: list[BrokenLink] = []

    for path in markdown_files:
        rel_path = _relative(path, vault_root)
        text = path.read_text(encoding="utf-8")
        outbound_targets: set[str] = set()
        for target in _extract_wikilinks(text):
            resolved = _resolve_link_target(target, path, files_by_stem, vault_root)
            if resolved is None:
                broken_links.append(BrokenLink(source_path=rel_path, target=target))
                continue
            outbound_targets.add(resolved)
            inbound_counts[resolved] = inbound_counts.get(resolved, 0) + 1
        outbound_map[rel_path] = outbound_targets

    orphaned_notes = [
        rel_path
        for rel_path, outbound_targets in outbound_map.items()
        if _eligible_for_orphan_check(rel_path)
        and not outbound_targets
        and inbound_counts.get(rel_path, 0) == 0
    ][:max_entries]

    stale_frontmatter = _scan_stale_frontmatter(vault_root, max_entries=max_entries)
    index_drift = _scan_index_drift(vault_root, max_entries=max_entries)
    aging_quarantine = _scan_aging_quarantine(
        vault_root,
        age_threshold_days=quarantine_age_days,
        max_entries=max_entries,
    )

    summary = {
        "date": run_date.isoformat(),
        "scan_scope": scan_scope,
        "broken_link_count": len(broken_links),
        "orphan_count": len(orphaned_notes),
        "stale_frontmatter_count": len(stale_frontmatter),
        "index_drift_count": len(index_drift),
        "aging_quarantine_count": len(aging_quarantine),
        "files_scanned": len(markdown_files),
        "files_read": sorted(dict.fromkeys(scan_scope + ["06_AGENTS/Knowledge-Taxonomy.md", "06_AGENTS/Vault-Map.md"])),
    }

    content = _render_markdown_report(
        summary=summary,
        broken_links=broken_links[:max_entries],
        orphaned_notes=orphaned_notes,
        stale_frontmatter=stale_frontmatter,
        index_drift=index_drift,
        aging_quarantine=aging_quarantine,
        quarantine_age_days=quarantine_age_days,
    )

    relative_output_path = Path("07_LOGS") / "Hygiene-Reports" / f"{run_date.isoformat()}-graph-hygiene.md"
    return {
        "handler_status": "executed",
        "workflow_id": "graph_hygiene",
        "date": run_date.isoformat(),
        "files_read": summary["files_read"],
        "summary": summary,
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
        value = int(str(raw_value))
    except ValueError as exc:
        raise WorkflowExecutionError(f"expected integer input, got {raw_value!r}") from exc
    return max(value, minimum)


def _resolve_scan_scope(raw_value: object) -> list[str]:
    if raw_value in (None, "", []):
        return list(DEFAULT_SCAN_SCOPE)
    if isinstance(raw_value, str):
        return [item.strip() for item in raw_value.split(",") if item.strip()]
    if isinstance(raw_value, Iterable):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    raise WorkflowExecutionError("scan_scope must be a comma-separated string or list of paths")


def _collect_markdown_files(vault_root: Path, scan_scope: list[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for raw_path in scan_scope:
        candidate = vault_root / raw_path
        if candidate.is_file() and candidate.suffix.lower() == ".md":
            rel_path = _relative(candidate, vault_root)
            if rel_path not in seen:
                files.append(candidate)
                seen.add(rel_path)
            continue
        if not candidate.is_dir():
            continue
        for path in sorted(candidate.rglob("*.md")):
            rel_path = _relative(path, vault_root)
            if _skip_runtime_noise(rel_path):
                continue
            if rel_path not in seen:
                files.append(path)
                seen.add(rel_path)
    return files


def _build_stem_index(markdown_files: list[Path], vault_root: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in markdown_files:
        rel_path = _relative(path, vault_root)
        stem = path.stem.lower()
        index.setdefault(stem, []).append(rel_path)
        rel_without_suffix = rel_path[:-3] if rel_path.lower().endswith(".md") else rel_path
        index.setdefault(rel_without_suffix.lower(), []).append(rel_path)
    return index


def _extract_wikilinks(text: str) -> list[str]:
    targets: list[str] = []
    for match in WIKILINK_RE.findall(text):
        target = match.split("|", 1)[0].split("#", 1)[0].strip()
        if not target or "://" in target:
            continue
        targets.append(target)
    return targets


def _resolve_link_target(
    target: str,
    source_path: Path,
    files_by_stem: dict[str, list[str]],
    vault_root: Path,
) -> str | None:
    normalized = target.strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return None

    direct_key = normalized.lower()
    if direct_key in files_by_stem:
        return sorted(files_by_stem[direct_key])[0]

    target_path = (source_path.parent / normalized).resolve()
    try:
        rel_target = target_path.relative_to(vault_root).as_posix()
    except ValueError:
        rel_target = normalized
    if not rel_target.lower().endswith(".md"):
        rel_target = f"{rel_target}.md"
    if rel_target.lower() in files_by_stem:
        return sorted(files_by_stem[rel_target.lower()])[0]

    stem_key = Path(normalized).stem.lower()
    matches = files_by_stem.get(stem_key, [])
    if len(matches) == 1:
        return matches[0]
    return None


def _eligible_for_orphan_check(rel_path: str) -> bool:
    path = Path(rel_path)
    if path.name in INDEX_FILENAMES or path.name.endswith("-Index.md"):
        return False
    if rel_path.startswith("07_LOGS/") or rel_path.startswith("99_ARCHIVE/"):
        return False
    return rel_path.startswith(("00_HOME/", "01_PROJECTS/", "02_KNOWLEDGE/", "04_SOPS/", "05_TEMPLATES/", "06_AGENTS/"))


def _scan_stale_frontmatter(vault_root: Path, max_entries: int) -> list[StaleFrontmatter]:
    knowledge_root = vault_root / "02_KNOWLEDGE"
    if not knowledge_root.exists():
        return []

    results: list[StaleFrontmatter] = []
    for path in sorted(knowledge_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(text)
        issues: list[str] = []
        required_fields = ["knowledge_class", "trust_tier", "verified_status", "domain"]
        if not frontmatter:
            issues.append("missing frontmatter block")
        else:
            for field_name in required_fields:
                if not frontmatter.get(field_name):
                    issues.append(f"missing required field: {field_name}")
            knowledge_class = str(frontmatter.get("knowledge_class") or "").strip()
            if knowledge_class == "generated-ideas":
                if not frontmatter.get("endorsement_status"):
                    issues.append("generated-ideas note missing endorsement_status")
                if not frontmatter.get("generated_with"):
                    issues.append("generated-ideas note missing generated_with")
            if "Generated-Ideas" in path.parts and knowledge_class and knowledge_class != "generated-ideas":
                issues.append("Generated-Ideas path does not match knowledge_class")
            if knowledge_class in {"canonical-state", "system-operational"}:
                issues.append("knowledge_class does not match 02_KNOWLEDGE placement")
        if issues:
            results.append(StaleFrontmatter(path=_relative(path, vault_root), issues=issues))
        if len(results) >= max_entries:
            break
    return results


def _scan_index_drift(vault_root: Path, max_entries: int) -> list[IndexDrift]:
    roots = [vault_root / "01_PROJECTS", vault_root / "02_KNOWLEDGE"]
    results: list[IndexDrift] = []
    for root in roots:
        if not root.exists():
            continue
        for directory in sorted([root] + [p for p in root.rglob("*") if p.is_dir()]):
            child_notes = [
                path for path in sorted(directory.glob("*.md"))
                if path.name not in INDEX_FILENAMES and not path.name.endswith("-Index.md")
            ]
            if not child_notes:
                continue
            index_file = _select_index_file(directory)
            if index_file is None:
                missing_entries = [note.name for note in child_notes[:5]]
                results.append(IndexDrift(index_path=f"{_relative(directory, vault_root)}/(missing index)", missing_entries=missing_entries))
            else:
                text = index_file.read_text(encoding="utf-8")
                missing_entries = [
                    note.name
                    for note in child_notes
                    if note.stem not in text and note.name not in text
                ][:5]
                if missing_entries:
                    results.append(IndexDrift(index_path=_relative(index_file, vault_root), missing_entries=missing_entries))
            if len(results) >= max_entries:
                return results
    return results


def _select_index_file(directory: Path) -> Path | None:
    for name in ("Index.md", "README.md", "Knowledge-Index.md"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    for path in directory.glob("*-Index.md"):
        return path
    return None


def _scan_aging_quarantine(
    vault_root: Path,
    age_threshold_days: int,
    max_entries: int,
) -> list[AgingCapture]:
    quarantine_root = vault_root / "03_INPUTS" / "00_QUARANTINE"
    if not quarantine_root.exists():
        return []

    now = datetime.now(UTC)
    aging: list[AgingCapture] = []
    for path in sorted(p for p in quarantine_root.rglob("*") if p.is_file()):
        age_days = (now - datetime.fromtimestamp(path.stat().st_mtime, UTC)).days
        if age_days < age_threshold_days:
            continue
        aging.append(AgingCapture(path=_relative(path, vault_root), days_old=age_days))
    aging.sort(key=lambda item: item.days_old, reverse=True)
    return aging[:max_entries]


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


def _skip_runtime_noise(rel_path: str) -> bool:
    return rel_path.startswith("runtime/aor/_tmp_tests/")


def _relative(path: Path, vault_root: Path) -> str:
    return str(path.relative_to(vault_root)).replace("\\", "/")


def _render_markdown_report(
    summary: dict,
    broken_links: list[BrokenLink],
    orphaned_notes: list[str],
    stale_frontmatter: list[StaleFrontmatter],
    index_drift: list[IndexDrift],
    aging_quarantine: list[AgingCapture],
    quarantine_age_days: int,
) -> str:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lines = [
        "---",
        "type: hygiene-report",
        "workflow: graph_hygiene",
        f"date: {summary['date']}",
        f"generated_at: {generated_at}",
        "source: aor",
        "mode: proposal-only",
        "---",
        "",
        f"# Graph Hygiene - {summary['date']}",
        "",
        "## Summary",
        f"- Files scanned: {summary['files_scanned']}",
        f"- Broken links: {summary['broken_link_count']}",
        f"- Orphaned notes: {summary['orphan_count']}",
        f"- Stale frontmatter findings: {summary['stale_frontmatter_count']}",
        f"- Index drift findings: {summary['index_drift_count']}",
        f"- Aging quarantine items ({quarantine_age_days}+ days): {summary['aging_quarantine_count']}",
        "",
        "## Scan Scope",
    ]
    lines.extend([f"- `{path}`" for path in summary["scan_scope"]])

    lines.extend(["", "## Broken Links"])
    if broken_links:
        for item in broken_links:
            lines.append(f"- `{item.source_path}` -> `[[{item.target}]]`")
    else:
        lines.append("- None found in the scanned scope.")

    lines.extend(["", "## Orphaned Notes"])
    if orphaned_notes:
        lines.extend([f"- `{path}`" for path in orphaned_notes])
    else:
        lines.append("- None found in the scanned scope.")

    lines.extend(["", "## Stale Frontmatter"])
    if stale_frontmatter:
        for item in stale_frontmatter:
            lines.append(f"- `{item.path}`: {', '.join(item.issues)}")
    else:
        lines.append("- None found in the scanned knowledge notes.")

    lines.extend(["", "## Index Drift"])
    if index_drift:
        for item in index_drift:
            lines.append(f"- `{item.index_path}` missing references for: {', '.join(item.missing_entries)}")
    else:
        lines.append("- None detected in the scanned project and knowledge directories.")

    lines.extend(["", "## Aging Quarantine Items"])
    if aging_quarantine:
        for item in aging_quarantine:
            lines.append(f"- `{item.path}` ({item.days_old} days)")
    else:
        lines.append("- No quarantine items crossed the aging threshold.")

    lines.extend(
        [
            "",
            "## Operator Follow-Up",
            "- Review broken links before any repair pass.",
            "- Confirm whether orphaned notes should be linked, indexed, archived, or left alone.",
            "- Apply frontmatter and index updates manually or via a separate explicit write pass.",
            "",
            "## Files Read",
        ]
    )
    lines.extend([f"- `{path}`" for path in summary["files_read"]])
    lines.append("")
    return "\n".join(lines)
