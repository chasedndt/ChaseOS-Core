"""Machine-readable audit writeback artifacts and Discord card rendering.

This module keeps ChaseOS audit writebacks in two synchronized layers:

1. a machine-readable JSON object / Markdown frontmatter artifact using
   ``schema_version: audit_writeback.v2``; and
2. a human-readable Discord smart-card payload rendered from the same object.

The helpers are intentionally local-only. They do not send Discord messages,
consume approvals, call providers, mutate canonical knowledge, or execute
runtime work. A caller can write the returned artifacts to an approved log path
or hand the ``discord.embed`` object to an already-authorized gateway layer.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any, Mapping, Sequence
from urllib.parse import quote

SCHEMA_VERSION = "audit_writeback.v2"
_STATUS = {"complete", "needs_review", "blocked", "failed", "in_progress"}
_AUTHORITY = {"read_only", "draft_write", "approval_gated", "blocked"}
_RISK = {"low", "medium", "high"}
_RESULT = {"pass", "fail", "blocked", "info", "not_run"}
_NEXT = {"continue", "approve", "review", "rerun", "route", "none"}
_STATUS_ICON = {
    "complete": "✅",
    "needs_review": "🟡",
    "blocked": "⛔",
    "failed": "❌",
    "in_progress": "🔵",
}
_STATUS_COLOR = {
    "complete": 0x2ECC71,
    "needs_review": 0xF1C40F,
    "blocked": 0xE67E22,
    "failed": 0xE74C3C,
    "in_progress": 0x3498DB,
}
_AUTHORITY_LABEL = {
    "read_only": "Read-only",
    "draft_write": "Draft write",
    "approval_gated": "Approval-gated",
    "blocked": "Blocked",
}


class AuditWritebackError(ValueError):
    """Raised when an audit writeback does not satisfy the v2 contract."""


@dataclass(frozen=True)
class WrittenAuditWriteback:
    """Paths written by ``write_audit_writeback_artifacts``."""

    json_path: Path
    markdown_path: Path
    discord_card_path: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "audit-writeback"


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _ensure_list(value: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in (value or [])]


def _evidence_item(
    label: str,
    result: str = "info",
    *,
    command: str = "",
    artifact: str = "",
    context: str = "",
) -> dict[str, str]:
    item: dict[str, str] = {"label": label, "result": result}
    if command:
        item["command"] = command
    if artifact:
        item["artifact"] = artifact
    if context:
        item["context"] = context
    return item


def _format_items(items: Sequence[Mapping[str, Any]], *, empty: str = "None") -> str:
    if not items:
        return empty
    lines: list[str] = []
    for item in items[:8]:
        result = str(item.get("result") or "info")
        label = str(item.get("label") or item.get("command") or item.get("artifact") or "evidence")
        suffix = ""
        if item.get("artifact"):
            suffix += f" — `{item['artifact']}`"
        elif item.get("command"):
            suffix += f" — `{item['command']}`"
        lines.append(f"- `{result}` {label}{suffix}")
    if len(items) > 8:
        lines.append(f"- … +{len(items) - 8} more")
    return "\n".join(lines)


def _control_plane_root() -> Path:
    """Return the ChaseOS control-plane root for resolving relative artifact paths."""

    return Path(__file__).resolve().parents[2]


def _windows_path_from_posix(path: Path) -> str | None:
    """Translate WSL-mounted Windows paths into Explorer-native full paths."""

    parts = path.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "mnt" and re.fullmatch(r"[a-zA-Z]", parts[2]):
        drive = parts[2].upper()
        tail = "\\".join(parts[3:])
        return f"{drive}:\\{tail}" if tail else f"{drive}:\\"
    return None


def _looks_like_windows_path(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]:[\\/]", value.strip()))


def _display_path(path: str) -> str:
    """Return the full operator-facing path, preferring Windows Explorer syntax."""

    raw = str(path or "").strip()
    if not raw:
        return raw
    if _looks_like_windows_path(raw):
        return str(PureWindowsPath(raw))
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _control_plane_root() / candidate
    candidate = candidate.resolve(strict=False)
    return _windows_path_from_posix(candidate) or str(candidate)


def _file_uri_for_display_path(display_path: str, *, parent: bool = False) -> str:
    """Build a file:// URI for Discord Markdown links without hiding the full path."""

    if _looks_like_windows_path(display_path):
        win = PureWindowsPath(display_path)
        target = win.parent if parent and win.parent != win else win
        drive = target.drive.rstrip(":").upper()
        parts = [quote(part) for part in target.parts[1:]]
        return f"file:///{drive}:/{'/'.join(parts)}"
    posix = Path(display_path)
    target = posix.parent if parent and posix.parent != posix else posix
    return "file://" + quote(str(target), safe="/:")


def _format_path_entry(path: str) -> str:
    """Render a styled artifact path row with full path plus an open-folder link."""

    full_path = _display_path(path)
    folder_uri = _file_uri_for_display_path(full_path, parent=True)
    return f"- 📄 `{full_path}` · [open folder]({folder_uri})"


def _format_paths(paths: Sequence[str], *, empty: str = "None") -> str:
    if not paths:
        return empty
    shown = [_format_path_entry(str(path)) for path in paths[:10]]
    if len(paths) > 10:
        shown.append(f"- … +{len(paths) - 10} more")
    return "\n".join(shown)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    text = str(value)
    if not text or any(ch in text for ch in ":#{}[]&,*?|-<>=!%@`\n\r\t") or text.strip() != text:
        return json.dumps(text)
    return text


def _yaml_dump(value: Any, indent: int = 0) -> str:
    spaces = " " * indent
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, nested in value.items():
            if isinstance(nested, (Mapping, list)):
                lines.append(f"{spaces}{key}:")
                lines.append(_yaml_dump(nested, indent + 2))
            else:
                lines.append(f"{spaces}{key}: {_yaml_scalar(nested)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{spaces}[]"
        lines = []
        for item in value:
            if isinstance(item, Mapping):
                lines.append(f"{spaces}-")
                lines.append(_yaml_dump(item, indent + 2))
            elif isinstance(item, list):
                lines.append(f"{spaces}-")
                lines.append(_yaml_dump(item, indent + 2))
            else:
                lines.append(f"{spaces}- {_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{spaces}{_yaml_scalar(value)}"


def render_discord_embed(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Render a Discord embed-compatible object from a v2 writeback payload.

    The returned dictionary is ready for a gateway layer to pass as an embed.
    This function performs no network send.
    """

    status = str(payload.get("status") or "needs_review")
    evidence = payload.get("evidence") or {}
    artifacts = payload.get("artifacts") or {}
    blockers = payload.get("blockers") or []
    next_action = payload.get("next_action") or {}
    changed = list(artifacts.get("changed_files") or [])
    generated = list(artifacts.get("generated_files") or [])
    logs = list(artifacts.get("logs") or [])
    screenshots = list(artifacts.get("screenshots") or [])
    artifact_count = len(changed) + len(generated) + len(logs) + len(screenshots)
    tests = list(evidence.get("tests") or [])
    commands = list(evidence.get("commands") or [])
    visual = list(evidence.get("visual_proof") or [])
    file_proof = list(evidence.get("file_proof") or [])

    fields = [
        {
            "name": "Status / Verdict",
            "value": f"**{status.replace('_', ' ').title()}** — {payload.get('verdict', '')}",
            "inline": False,
        },
        {
            "name": "Runtime / Authority / Risk",
            "value": (
                f"`{payload.get('runtime')}` · "
                f"{_AUTHORITY_LABEL.get(str(payload.get('authority_scope')), payload.get('authority_scope'))} · "
                f"{str(payload.get('risk_level')).title()} risk"
            ),
            "inline": False,
        },
        {"name": "Evidence", "value": _format_items(tests + commands + visual + file_proof), "inline": False},
        {
            "name": "Artifacts",
            "value": f"{artifact_count} artifact(s)\n{_format_paths(changed + generated + logs + screenshots)}",
            "inline": False,
        },
        {
            "name": "Risk / Blockers",
            "value": "None" if not blockers else _format_items([
                {"label": b.get("blocker", "blocker"), "result": "blocked", "artifact": b.get("owner_surface", "")}
                for b in blockers
            ]),
            "inline": False,
        },
        {
            "name": "Next Action",
            "value": f"`{next_action.get('type', 'none')}` {next_action.get('label', '')}\n{next_action.get('target', '')}".strip(),
            "inline": False,
        },
    ]
    return {
        "title": f"{_STATUS_ICON.get(status, '🧾')} {payload.get('title', 'Audit Writeback')}",
        "description": str((payload.get("summary") or {}).get("one_line") or payload.get("verdict") or ""),
        "color": _STATUS_COLOR.get(status, 0x95A5A6),
        "fields": fields,
        "footer": {"text": f"{payload.get('schema_version')} · {payload.get('writeback_id')} · {payload.get('created_at')}"},
    }


def render_discord_card(payload: Mapping[str, Any]) -> str:
    """Render a human-readable Markdown card for Discord fallback output."""

    status = str(payload.get("status") or "needs_review")
    icon = _STATUS_ICON.get(status, "🧾")
    evidence = payload.get("evidence") or {}
    artifacts = payload.get("artifacts") or {}
    blockers = payload.get("blockers") or []
    next_action = payload.get("next_action") or {}
    artifact_paths = (
        list(artifacts.get("changed_files") or [])
        + list(artifacts.get("generated_files") or [])
        + list(artifacts.get("logs") or [])
        + list(artifacts.get("screenshots") or [])
    )
    evidence_items = (
        list(evidence.get("tests") or [])
        + list(evidence.get("commands") or [])
        + list(evidence.get("visual_proof") or [])
        + list(evidence.get("file_proof") or [])
    )
    blocker_text = "None" if not blockers else "\n".join(
        f"- {b.get('blocker', 'blocker')} · owner: `{b.get('owner_surface', '')}` · proof: {b.get('minimum_proof_needed', '')}"
        for b in blockers
    )
    return "\n".join(
        [
            f"## {icon} {payload.get('title', 'Audit Writeback')}",
            "",
            "### Status / Verdict",
            f"**Status:** `{status}`  ",
            f"**Verdict:** {payload.get('verdict', '')}",
            "",
            "### Summary",
            str((payload.get("summary") or {}).get("operator_readable") or (payload.get("summary") or {}).get("one_line") or ""),
            "",
            "### Evidence",
            _format_items(evidence_items),
            "",
            "### Files / Artifacts",
            _format_paths(artifact_paths),
            "",
            "### Risk / Blockers",
            f"**Authority:** `{payload.get('authority_scope')}` · **Risk:** `{payload.get('risk_level')}`",
            blocker_text,
            "",
            "### Next Action",
            f"`{next_action.get('type', 'none')}` — {next_action.get('label', '')}",
            str(next_action.get("target", "")),
        ]
    ).strip() + "\n"


def build_audit_writeback(
    *,
    title: str,
    runtime: str,
    lane: str = "audit-writeback",
    status: str,
    verdict: str,
    authority_scope: str,
    risk_level: str,
    summary_one_line: str,
    summary_operator_readable: str = "",
    tests: Sequence[Mapping[str, Any]] | None = None,
    visual_proof: Sequence[Mapping[str, Any]] | None = None,
    file_proof: Sequence[Mapping[str, Any]] | None = None,
    commands: Sequence[Mapping[str, Any]] | None = None,
    changed_files: Sequence[str] | None = None,
    generated_files: Sequence[str] | None = None,
    logs: Sequence[str] | None = None,
    screenshots: Sequence[str] | None = None,
    blockers: Sequence[Mapping[str, Any]] | None = None,
    next_action_type: str = "none",
    next_action_label: str = "No operator action required.",
    next_action_target: str = "",
    created_at: str | None = None,
    writeback_id: str | None = None,
) -> dict[str, Any]:
    """Build, validate, and render a v2 audit writeback object."""

    created = created_at or _utc_now()
    base_id = writeback_id or f"{_slug(title)}-{_short_hash(title + created)}"
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "writeback_id": base_id,
        "title": title,
        "runtime": runtime,
        "lane": lane,
        "status": status,
        "verdict": verdict,
        "authority_scope": authority_scope,
        "risk_level": risk_level,
        "created_at": created,
        "summary": {
            "one_line": summary_one_line,
            "operator_readable": summary_operator_readable or summary_one_line,
        },
        "evidence": {
            "tests": _ensure_list(tests),
            "visual_proof": _ensure_list(visual_proof),
            "file_proof": _ensure_list(file_proof),
            "commands": _ensure_list(commands),
        },
        "artifacts": {
            "changed_files": list(changed_files or []),
            "generated_files": list(generated_files or []),
            "logs": list(logs or []),
            "screenshots": list(screenshots or []),
        },
        "blockers": [dict(item) for item in (blockers or [])],
        "next_action": {
            "type": next_action_type,
            "label": next_action_label,
            "target": next_action_target,
        },
    }
    validate_audit_writeback(payload)
    rendered = deepcopy(payload)
    rendered["discord"] = {
        "embed": render_discord_embed(rendered),
        "card_markdown": render_discord_card(rendered),
    }
    validate_audit_writeback(rendered)
    return rendered


def validate_audit_writeback(payload: Mapping[str, Any]) -> None:
    """Validate core v2 fields without requiring a JSON-schema dependency."""

    required = {
        "schema_version",
        "writeback_id",
        "title",
        "runtime",
        "lane",
        "status",
        "verdict",
        "authority_scope",
        "risk_level",
        "created_at",
        "summary",
        "evidence",
        "artifacts",
        "blockers",
        "next_action",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise AuditWritebackError(f"missing required audit writeback fields: {', '.join(missing)}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise AuditWritebackError(f"schema_version must be {SCHEMA_VERSION}")
    if payload.get("status") not in _STATUS:
        raise AuditWritebackError(f"invalid status: {payload.get('status')}")
    if payload.get("authority_scope") not in _AUTHORITY:
        raise AuditWritebackError(f"invalid authority_scope: {payload.get('authority_scope')}")
    if payload.get("risk_level") not in _RISK:
        raise AuditWritebackError(f"invalid risk_level: {payload.get('risk_level')}")
    if not str(payload.get("title") or "").strip() or not str(payload.get("verdict") or "").strip():
        raise AuditWritebackError("title and verdict are required")
    summary = payload.get("summary")
    if not isinstance(summary, Mapping) or not summary.get("one_line") or not summary.get("operator_readable"):
        raise AuditWritebackError("summary.one_line and summary.operator_readable are required")
    evidence = payload.get("evidence")
    if not isinstance(evidence, Mapping):
        raise AuditWritebackError("evidence must be an object")
    for bucket in ("tests", "visual_proof", "file_proof", "commands"):
        if bucket not in evidence or not isinstance(evidence[bucket], list):
            raise AuditWritebackError(f"evidence.{bucket} must be a list")
        for item in evidence[bucket]:
            if not isinstance(item, Mapping) or not item.get("label"):
                raise AuditWritebackError(f"evidence.{bucket} items require label")
            if item.get("result") not in _RESULT:
                raise AuditWritebackError(f"invalid evidence result in {bucket}: {item.get('result')}")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise AuditWritebackError("artifacts must be an object")
    for bucket in ("changed_files", "generated_files", "logs", "screenshots"):
        if bucket not in artifacts or not isinstance(artifacts[bucket], list):
            raise AuditWritebackError(f"artifacts.{bucket} must be a list")
    if not isinstance(payload.get("blockers"), list):
        raise AuditWritebackError("blockers must be a list")
    for blocker in payload.get("blockers", []):
        for field in ("blocker", "owner_surface", "required_approval", "minimum_proof_needed"):
            if not isinstance(blocker, Mapping) or field not in blocker:
                raise AuditWritebackError(f"blocker items require {field}")
    next_action = payload.get("next_action")
    if not isinstance(next_action, Mapping) or next_action.get("type") not in _NEXT:
        raise AuditWritebackError("next_action.type is invalid")
    if "discord" in payload:
        discord = payload["discord"]
        if not isinstance(discord, Mapping) or not isinstance(discord.get("embed"), Mapping) or not discord.get("card_markdown"):
            raise AuditWritebackError("discord.embed and discord.card_markdown are required when discord is present")


def render_frontmatter_markdown(payload: Mapping[str, Any]) -> str:
    """Render the machine-readable payload as Markdown with YAML frontmatter."""

    validate_audit_writeback(payload)
    frontmatter_keys = [
        "schema_version",
        "writeback_id",
        "title",
        "runtime",
        "lane",
        "status",
        "verdict",
        "authority_scope",
        "risk_level",
        "created_at",
        "summary",
        "evidence",
        "artifacts",
        "blockers",
        "next_action",
    ]
    frontmatter = {key: deepcopy(payload[key]) for key in frontmatter_keys}
    card = payload.get("discord", {}).get("card_markdown") or render_discord_card(payload)
    return f"---\n{_yaml_dump(frontmatter)}\n---\n\n{card}"


def write_audit_writeback_artifacts(payload: Mapping[str, Any], output_dir: str | Path) -> WrittenAuditWriteback:
    """Write JSON, frontmatter Markdown, and Discord-card Markdown artifacts."""

    validate_audit_writeback(payload)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stem = _slug(str(payload.get("writeback_id") or payload.get("title") or "audit-writeback"))
    json_path = output / f"{stem}.json"
    markdown_path = output / f"{stem}.md"
    discord_card_path = output / f"{stem}.discord.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_frontmatter_markdown(payload), encoding="utf-8")
    discord_card_path.write_text(str(payload.get("discord", {}).get("card_markdown") or render_discord_card(payload)), encoding="utf-8")
    return WrittenAuditWriteback(json_path=json_path, markdown_path=markdown_path, discord_card_path=discord_card_path)


__all__ = [
    "AuditWritebackError",
    "SCHEMA_VERSION",
    "WrittenAuditWriteback",
    "build_audit_writeback",
    "render_discord_card",
    "render_discord_embed",
    "render_frontmatter_markdown",
    "validate_audit_writeback",
    "write_audit_writeback_artifacts",
]
