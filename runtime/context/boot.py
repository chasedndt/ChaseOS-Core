"""ChaseOS Context Boot Protocol.

Every ChaseOS runtime — regardless of harness (Claude Code, OpenClaw/Discord,
MCP, local model, future runners) — must load a ContextBundle before executing
any workflow or action. This module is the single authoritative source for that
boot-time context load.

Boot status semantics:
    "ok"       — Now.md + adapter manifest + contract all read successfully.
    "degraded" — Now.md read but one or more optional sources missing.
    "failed"   — Now.md not found. Runtime is flying blind; callers must escalate.

Public API:
    load_boot_context(vault_root, runtime_id) -> ContextBundle
    ContextBundle.to_frame() -> str   (compact text for prompt injection)
    ContextBundle.to_dict()  -> dict  (serialisable for audit records / MCP responses)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml  # type: ignore
except Exception:
    _yaml = None


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _section_after_heading(text: str, heading: str) -> str:
    """Return the text content under a Markdown heading (stops at next heading)."""
    lines = text.splitlines()
    capture = False
    captured: list[str] = []
    needle = heading.lower().strip()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            # Normalise heading: strip hashes, brackets, whitespace
            normalised = stripped.lstrip("#").strip().strip("[]").lower()
            if capture and normalised != needle:
                break
            capture = normalised == needle or normalised.startswith(needle)
            continue
        if capture and stripped:
            captured.append(stripped)
    return "\n".join(captured)


def _first_bullet(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and not stripped.startswith("- [ ]"):
            return stripped[2:].strip()
    return ""


def _extract_current_phase(now_text: str) -> str:
    # Try heading-based first
    under_heading = _section_after_heading(now_text, "Current Phase")
    if under_heading:
        return under_heading.splitlines()[0].strip()

    # Try inline bold pattern: **Current Phase:** Phase 9 ...
    for line in now_text.splitlines():
        m = re.search(r"\*\*Current Phase[:\*]+\*\*\s*(.*)", line, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.search(r"Current Phase:\s*(.*)", line, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _extract_sprint_focus(now_text: str) -> str:
    active_now = _section_after_heading(now_text, "Active Now")
    focus = _first_bullet(active_now)
    if focus:
        return focus
    return _first_bullet(now_text)


def _frontmatter_field(text: str, key: str) -> str:
    """Extract a field from YAML frontmatter (between --- delimiters)."""
    lines = text.splitlines()
    in_front = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            if not in_front:
                in_front = True
                continue
            break
        if in_front and stripped.startswith(f"{key}:"):
            value = stripped[len(f"{key}:"):].strip().strip('"').strip("'")
            return value
    return ""


def _load_yaml_simple(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if _yaml is not None:
        try:
            data = _yaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    # Minimal fallback: key: value on top-level lines only
    result: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped or line.startswith(" "):
            continue
        if ":" in stripped:
            k, _, v = stripped.partition(":")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _extract_write_targets(manifest: dict[str, Any]) -> list[str]:
    """Resolve allowed write targets from adapter manifest allowed_write_targets block."""
    alias_map = {
        "standard_outputs": "07_LOGS/",
        "operator_briefs": "07_LOGS/Operator-Briefs/",
        "runtime_activity": "07_LOGS/Agent-Activity/",
        "draft_outputs": "07_LOGS/Operator-Briefs/_drafts/",
        "archive_notes": "99_ARCHIVE/Documentation-History/",
        "project_os_files": "01_PROJECTS/",
        "knowledge_notes": "02_KNOWLEDGE/",
        "inputs_folder": "03_INPUTS/",
        "protected_files": "[protected-blocked]",
    }
    allowed = manifest.get("allowed_write_targets", {})
    if not isinstance(allowed, dict):
        return []
    return [alias_map.get(k, k) for k, v in allowed.items() if v is True]


def _get_carry_forward(vault_root: Path) -> str:
    """Extract the [CARRY-FORWARD] section from the most recent operator_close_day brief."""
    briefs_dir = vault_root / "07_LOGS" / "Operator-Briefs"
    if not briefs_dir.exists():
        return ""
    candidates = sorted(briefs_dir.glob("*-operator-close-day.md"))
    if not candidates:
        return ""
    text = _read_text(candidates[-1])
    section = _section_after_heading(text, "carry-forward")
    if not section:
        return ""
    # Filter out the > quoted preamble lines, keep bullet lines
    bullets = [
        line.strip() for line in section.splitlines()
        if line.strip().startswith("- ") and "status:none" not in line.lower()
    ]
    return " | ".join(bullets[:5]) if bullets else "(none recorded)"


# ── ContextBundle ──────────────────────────────────────────────────────────────

@dataclass
class ContextBundle:
    """The ChaseOS pre-execution context loaded for every runtime before action."""

    runtime_id: str
    current_phase: str
    sprint_focus: str
    trust_ceiling: str
    approval_mode: str
    protected_file_behavior: str
    allowed_write_targets: list[str]
    assistant_contract_version: str
    carry_forward: str
    boot_status: str           # "ok" | "degraded" | "failed"
    boot_warnings: list[str] = field(default_factory=list)
    sources_read: list[str] = field(default_factory=list)
    timestamp: str = ""

    def to_frame(self) -> str:
        """Render a compact boot frame for injection into any LLM prompt."""
        lines = [
            "## ChaseOS Context Boot",
            f"- Runtime: {self.runtime_id}",
            f"- Phase: {self.current_phase or '(unknown — read Now.md)'}",
            f"- Sprint focus: {self.sprint_focus or '(unknown — read Now.md)'}",
            f"- Trust ceiling: {self.trust_ceiling}",
            f"- Approval mode: {self.approval_mode}",
            f"- Protected files: {self.protected_file_behavior}",
            f"- Contract: Assistant-Contract.md {self.assistant_contract_version}",
            f"- Carry-forward: {self.carry_forward or '(none recorded)'}",
        ]
        if self.boot_warnings:
            lines.append(f"- Boot warnings: {'; '.join(self.boot_warnings)}")
        lines.append(f"- Boot status: {self.boot_status.upper()}")
        lines += [
            "",
            "Before executing anything:",
            "  1. Read 00_HOME/Now.md to confirm current phase and sprint focus.",
            "  2. Read the relevant Project-OS file for any project you will touch.",
            "  3. Write only to declared writeback targets for your role card.",
            "  4. Escalate rather than guess on ambiguity, scope creep, or protected files.",
            "  5. Do not act on vault state not explicitly read in this session.",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "current_phase": self.current_phase,
            "sprint_focus": self.sprint_focus,
            "trust_ceiling": self.trust_ceiling,
            "approval_mode": self.approval_mode,
            "protected_file_behavior": self.protected_file_behavior,
            "allowed_write_targets": self.allowed_write_targets,
            "assistant_contract_version": self.assistant_contract_version,
            "carry_forward": self.carry_forward,
            "boot_status": self.boot_status,
            "boot_warnings": self.boot_warnings,
            "sources_read": self.sources_read,
            "timestamp": self.timestamp,
        }


# ── Public API ────────────────────────────────────────────────────────────────

def load_boot_context(
    vault_root: Path,
    runtime_id: str = "openclaw",
) -> ContextBundle:
    """
    Load the ChaseOS pre-execution context bundle.

    Reads Now.md (phase + sprint focus), the adapter manifest for runtime_id
    (trust ceiling, approval mode, write targets), Assistant-Contract.md (version),
    and the most recent operator_close_day brief (carry-forward loops).

    Boot status:
        "ok"       — all primary sources read.
        "degraded" — Now.md present but optional sources missing.
        "failed"   — Now.md not found; caller must escalate, not proceed.
    """
    warnings: list[str] = []
    sources: list[str] = []

    # ── 1. Now.md — canonical phase + sprint truth (primary source) ───────────
    now_path = vault_root / "00_HOME" / "Now.md"
    current_phase = ""
    sprint_focus = ""
    if now_path.exists():
        now_text = _read_text(now_path)
        current_phase = _extract_current_phase(now_text)
        sprint_focus = _extract_sprint_focus(now_text)
        sources.append("00_HOME/Now.md")
    else:
        warnings.append("00_HOME/Now.md not found — runtime has no phase/sprint anchor")

    # ── 2. Adapter manifest — trust ceiling + write policy ────────────────────
    trust_ceiling = "tier-4"
    approval_mode = "explicit"
    protected_file_behavior = "fail-closed"
    allowed_write_targets: list[str] = []

    manifest_path = vault_root / "runtime" / "policy" / "adapters" / f"{runtime_id}.yaml"
    if manifest_path.exists():
        manifest = _load_yaml_simple(manifest_path)
        trust_ceiling = str(manifest.get("trust_ceiling", "tier-4"))
        approval_mode = str(manifest.get("approval_mode", "explicit"))
        protected_file_behavior = str(manifest.get("protected_file_behavior", "fail-closed"))
        if protected_file_behavior == "block":
            protected_file_behavior = "fail-closed"
        allowed_write_targets = _extract_write_targets(manifest)
        sources.append(f"runtime/policy/adapters/{runtime_id}.yaml")
    else:
        # Try any available manifest as a fallback
        any_manifest = sorted((vault_root / "runtime" / "policy" / "adapters").glob("*.yaml"))
        if any_manifest:
            manifest = _load_yaml_simple(any_manifest[0])
            trust_ceiling = str(manifest.get("trust_ceiling", "tier-4"))
            approval_mode = str(manifest.get("approval_mode", "explicit"))
            warnings.append(
                f"adapter manifest '{runtime_id}.yaml' not found; "
                f"used '{any_manifest[0].name}' as fallback"
            )
            sources.append(f"runtime/policy/adapters/{any_manifest[0].name} (fallback)")
        else:
            warnings.append(f"no adapter manifest found for runtime_id='{runtime_id}'")

    # ── 3. Assistant-Contract.md — binding contract version ───────────────────
    contract_path = vault_root / "00_HOME" / "Assistant-Contract.md"
    contract_version = "unknown"
    if contract_path.exists():
        contract_text = _read_text(contract_path)
        contract_version = _frontmatter_field(contract_text, "version") or "unknown"
        sources.append("00_HOME/Assistant-Contract.md")
    else:
        warnings.append("00_HOME/Assistant-Contract.md not found")

    # ── 4. Carry-forward — open loops from last close_day (optional) ──────────
    carry_forward = _get_carry_forward(vault_root)

    # ── Determine boot status ─────────────────────────────────────────────────
    now_found = "00_HOME/Now.md" in sources
    if not now_found:
        boot_status = "failed"
    elif warnings:
        boot_status = "degraded"
    else:
        boot_status = "ok"

    return ContextBundle(
        runtime_id=runtime_id,
        current_phase=current_phase,
        sprint_focus=sprint_focus,
        trust_ceiling=trust_ceiling,
        approval_mode=approval_mode,
        protected_file_behavior=protected_file_behavior,
        allowed_write_targets=allowed_write_targets,
        assistant_contract_version=contract_version,
        carry_forward=carry_forward,
        boot_status=boot_status,
        boot_warnings=warnings,
        sources_read=sources,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
