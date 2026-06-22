"""Bounded Hermes Studio Chat capability layer.

This module handles Studio Chat control-plane style requests before the generic
Hermes synthesis bridge. It intentionally stays inside read-only / preview-only
behavior: no provider calls, no shell execution, no approval consumption, no
canonical promotion, and no protected-file mutation.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ALLOWED_ACTIONS = {
    "capabilities",
    "help",
    "status",
    "readiness",
    "proposal",
    "confirm",
    "handoff",
    "audit",
    "blockers",
    "commands",
    "authority",
    "actions",
    "governance",
    "model-call",
    "provider-model-call",
    "provider-call",
    "shell",
    "run",
    "approve",
    "promote",
    "send",
    "grant-runtime-authority",
    "grant-authority",
    "protected-mutation",
    "mutate-protected",
}

_STATUS_WORDS = ("status", "readiness", "ready", "health", "connected", "daemon")
_CAPABILITY_WORDS = ("capabilities", "capability", "what can", "can you implement", "discord control plane")
_AUTHORITY_WORDS = (
    "main control plane",
    "primary control plane",
    "shell/runtime execution",
    "approval consumption",
    "protected-file mutation",
    "canonical knowledge promotion",
    "external connector sends",
    "granting new runtime authority",
    "aor governance",
    "chaseos gate",
)
_PROPOSAL_WORDS = ("proposal", "preview", "plan", "action preview")
_HANDOFF_WORDS = ("handoff", "delegate", "runtime execution", "agent bus task")
_BLOCKER_WORDS = ("blocker", "blocked", "cannot", "dependency")
# Keep confirmation routing narrow. Natural chat often says "confirm" as a
# synonym for "tell me whether this works"; that should stay on the live Hermes
# response bridge instead of becoming an approval-consumption preview.
_CONFIRM_WORDS = ("approve", "approval", "execute this", "do it")


@dataclass(frozen=True)
class StudioChatCapabilityResult:
    ok: bool
    text: str
    action: str
    authority: dict[str, bool]

    def as_bridge_packet(self, *, session_id: str = "") -> dict[str, Any]:
        return {
            "ok": self.ok,
            "text": self.text,
            "runtime": "Hermes",
            "session_id": session_id,
            "provider_detail_redacted": True,
            "bridge": "hermes_studio_capability_layer",
            "capability_action": self.action,
            "authority": self.authority,
        }


def _authority() -> dict[str, bool]:
    return {
        "provider_call_performed": False,
        "shell_command_performed": False,
        "approval_consumed": False,
        "canonical_mutation_performed": False,
        "protected_file_mutation_performed": False,
        "agent_bus_task_created": False,
        "preview_only": True,
        "runtime_daemon_path_required_for_effects": True,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_message(message: str) -> str:
    return re.sub(r"\s+", " ", str(message or "").strip())


def _contains_trigger(lower: str, words: tuple[str, ...]) -> bool:
    for word in words:
        trigger = str(word or "").strip().lower()
        if not trigger:
            continue
        # Short command words such as "plan" must be word-bounded so
        # "control plane" does not incorrectly route a normal chat message to
        # the proposal-preview capability layer.
        if re.fullmatch(r"[a-z0-9_-]+", trigger):
            if re.search(rf"(?<![a-z0-9_-]){re.escape(trigger)}(?![a-z0-9_-])", lower):
                return True
        elif trigger in lower:
            return True
    return False


def _extract_action(message: str) -> tuple[str | None, str]:
    text = _clean_message(message)
    lower = text.lower()
    if lower.startswith("/"):
        parts = text[1:].split(" ", 1)
        action = parts[0].strip().lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if action in _ALLOWED_ACTIONS:
            normalized_action = "capabilities" if action == "help" else action
            normalized_action = "authority" if normalized_action in {"actions", "governance"} else normalized_action
            normalized_action = "shell" if normalized_action == "run" else normalized_action
            normalized_action = "grant-runtime-authority" if normalized_action == "grant-authority" else normalized_action
            normalized_action = "protected-mutation" if normalized_action == "mutate-protected" else normalized_action
            normalized_action = "model-call" if normalized_action in {"provider-model-call", "provider-call"} else normalized_action
            return normalized_action, rest
        return "commands", text
    if _contains_trigger(lower, _AUTHORITY_WORDS):
        return "authority", text
    if _contains_trigger(lower, _CAPABILITY_WORDS):
        return "capabilities", text
    if _contains_trigger(lower, _STATUS_WORDS):
        return "readiness", text
    if _contains_trigger(lower, _PROPOSAL_WORDS):
        return "proposal", text
    if _contains_trigger(lower, _HANDOFF_WORDS):
        return "handoff", text
    if _contains_trigger(lower, _CONFIRM_WORDS):
        return "confirm", text
    if _contains_trigger(lower, _BLOCKER_WORDS):
        return "blockers", text
    return None, text


def _safe_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _count_matching_files(root: Path, patterns: tuple[str, ...]) -> int:
    total = 0
    for pattern in patterns:
        try:
            total += sum(1 for _ in root.glob(pattern))
        except Exception:
            continue
    return total


def _agent_bus_snapshot(root: Path) -> dict[str, Any]:
    try:
        from runtime.agent_bus.bus import list_tasks

        tasks = list_tasks(root)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "tasks": [], "counts": {}}
    counts = Counter(str(task.get("status") or "unknown").lower() for task in tasks)
    recipient_counts = Counter(str(task.get("recipient") or "unknown") for task in tasks)
    recent = []
    for task in sorted(tasks, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)[:5]:
        recent.append(
            {
                "task_id": task.get("task_id"),
                "recipient": task.get("recipient"),
                "status": task.get("status"),
                "intent": task.get("intent"),
                "preview": str(task.get("request") or "")[:90],
            }
        )
    return {
        "ok": True,
        "tasks": tasks,
        "counts": dict(counts),
        "recipient_counts": dict(recipient_counts),
        "recent": recent,
    }


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none observed"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _capabilities_text() -> str:
    return """## Hermes Studio Chat capability layer

Available now through the Agent Bus / Hermes runtime-daemon path:

- `/capabilities` — show this bounded command map.
- `/status` or `/readiness` — inspect Studio/Hermes/Agent Bus readiness from local vault state.
- `/proposal <goal>` — produce a preview-only action proposal with required approvals and blocked effects.
- `/handoff <runtime>: <objective>` — draft a handoff packet preview for runtime execution; it does not enqueue automatically.
- `/confirm <proposal>` — explain the operator-confirmation boundary; this lane does not consume approvals.
- `/blockers` — report lower-phase/backend blockers that prevent direct execution.
- `/audit` — show audit-visible response format and authority flags.
- `/authority` or `/actions` — show the ChaseOS main-control-plane authority catalog for shell/runtime execution, approval consumption, protected-file mutation, canonical promotion, external sends, and runtime authority grants.
- `/model-call` — show provider/model call readiness plus one narrow approved-call contract preview; no provider call runs.
- `/shell <request>` or `/run <request>` — preview a gated shell/runtime execution envelope; no shell runs from Chat.
- `/approve <fingerprint>` — preview approval consumption requirements; no approval is consumed.
- `/promote <source> -> <target>` — preview canonical-promotion requirements; no canonical mutation occurs.
- `/send <destination> <message>` — preview an external-send envelope; no delivery occurs.
- `/grant-runtime-authority <runtime> <authority>` — preview a governance/manifest authority-diff envelope; no authority is granted.
- `/protected-mutation <path> <operation>` — preview a protected-file mutation envelope; no protected file is changed.

Mapped from the Discord control-plane experience into Studio Chat:

1. Chat-command UX patterns.
2. Inspect status/readiness panels.
3. Proposal and action preview surfaces.
4. Operator-confirmation flows.
5. Handoff packets for runtime execution.
6. Audit-visible response formatting.
7. Capability mapping from Discord concepts into Studio Chat concepts.
8. Blocker reports for anything requiring lower-phase backend authority.

Authority boundary: this chat layer can inspect, preview, format, and route intent. It does not execute shell/runtime actions, consume approvals, mutate protected files, promote canonical knowledge, grant new runtime authority, or bypass ChaseOS Gate/AOR governance."""


def _readiness_text(root: Path) -> str:
    bus = _agent_bus_snapshot(root)
    agent_activity_count = _count_matching_files(root, ("07_LOGS/Agent-Activity/*.md",))
    build_log_count = _count_matching_files(root, ("07_LOGS/Build-Logs/*.md",))
    approval_count = _count_matching_files(
        root,
        (
            "07_LOGS/**/approval*.md",
            "07_LOGS/**/Approval*.md",
            "runtime/**/approval*.json",
            "runtime/**/approval*.md",
        ),
    )
    lines = [
        "## Hermes Studio readiness snapshot",
        "",
        f"- generated_at: `{_utc_now()}`",
        f"- vault_root: `{root}`",
        "- Studio Chat route: `Studio Chat → Agent Bus → Hermes daemon → Hermes bridge → Agent Bus result`",
        "- direct provider call from Studio: `false`",
        "- terminal/page-load launcher action: `false`",
    ]
    if bus.get("ok"):
        lines.extend(
            [
                f"- Agent Bus task statuses: {_format_counts(bus.get('counts') or {})}",
                f"- Agent Bus recipients: {_format_counts(bus.get('recipient_counts') or {})}",
            ]
        )
        recent = bus.get("recent") or []
        if recent:
            lines.append("- Recent bus tasks:")
            for task in recent:
                lines.append(
                    f"  - `{task.get('task_id')}` → {task.get('recipient')} / {task.get('status')} / {task.get('preview')}"
                )
    else:
        lines.append(f"- Agent Bus snapshot: blocked by `{bus.get('error')}`")
    lines.extend(
        [
            f"- Agent-Activity artifacts observed: {agent_activity_count}",
            f"- Build-log artifacts observed: {build_log_count}",
            f"- Approval-looking artifacts observed: {approval_count}",
            "",
            "Readiness meaning: Hermes can reply and provide bounded control-plane previews from Studio Chat. Effectful work still needs the appropriate runtime daemon/action executor and Gate/operator approval path.",
        ]
    )
    return "\n".join(lines)


def _proposal_text(rest: str) -> str:
    goal = rest or "No goal supplied. Add a goal after `/proposal`."
    return f"""## Proposal preview — no effects performed

Goal:
> {goal}

Safe Studio Chat action envelope:

- intent_class: `proposal_preview`
- proposed_runtime_route: `Agent Bus / selected runtime daemon`
- writes_performed: `false`
- provider_call_performed_by_studio: `false`
- approval_consumed: `false`
- canonical_mutation_performed: `false`

Execution ladder:
1. Convert the request into a bounded action packet.
2. Identify required runtime owner and authority ceiling.
3. Present preview, risks, expected outputs, and blocked effects.
4. Require explicit operator/Gate approval before any effectful executor consumes the packet.
5. Write audit evidence after execution, not before claiming completion.

Blocked from this chat lane: shell execution, approval consumption, protected-file mutation, canonical promotion, external sends, and broad runtime authority grants."""


def _handoff_text(rest: str) -> str:
    target = "selected-runtime"
    objective = rest or "No objective supplied. Add `/handoff Hermes: <objective>` or `/handoff OpenClaw: <objective>`."
    if ":" in objective:
        raw_target, raw_objective = objective.split(":", 1)
        target = raw_target.strip() or target
        objective = raw_objective.strip() or objective
    return f"""## Runtime handoff packet preview — not enqueued

recipient: `{target}`
task_type: `operator_request_preview`
source_surface: `studio_chat`
objective:
> {objective}

Expected output:
- bounded result summary
- proof artifact or blocker report
- authority flags
- audit path if an approved executor later runs

Operator confirmation required before enqueue/execution:
- exact runtime recipient
- allowed files/surfaces
- allowed tools/effects
- approval artifact or Gate decision when required

Current result: preview only. No Agent Bus task was created by this command."""


def _confirm_text(rest: str) -> str:
    target = rest or "the referenced proposal"
    return f"""## Operator-confirmation boundary

You referenced confirmation for:
> {target}

This Hermes Studio Chat capability layer can acknowledge and format confirmation intent, but it does not consume approvals or execute effects.

To make this executable, ChaseOS needs a separate approval-consuming executor path with:
1. proposal/action packet fingerprint,
2. matching operator/Gate approval artifact,
3. exact-once consumption marker,
4. runtime-owned executor,
5. post-action audit writeback.

Current result: no approval was consumed and no action was executed."""


def _control_plane_authority_catalog() -> list[dict[str, str]]:
    """Return ChaseOS main-control-plane authorities as gated action classes.

    These are capabilities ChaseOS should own as the primary control plane, but
    they are not direct chat effects. Each one requires an explicit executor,
    Gate/approval evidence where applicable, and audit proof before target-side
    mutation can be claimed.
    """
    return [
        {
            "capability": "shell/runtime execution",
            "control_plane_status": "allowed_as_gated_executor",
            "direct_chat_status": "forbidden",
            "required_gate": "AOR workflow manifest + runtime-owned executor + scoped tool/effect policy + post-run audit",
            "why_not_direct": "Direct chat-triggered shell would bypass workflow manifests, least-authority tool scopes, and terminal-spawn safety.",
        },
        {
            "capability": "approval consumption",
            "control_plane_status": "allowed_as_exact_once_gate",
            "direct_chat_status": "forbidden",
            "required_gate": "proposal fingerprint + matching operator/Gate approval artifact + exact-once consumption marker + replay refusal",
            "why_not_direct": "A chat message saying approve is not itself a durable Gate decision and must not self-authorize side effects.",
        },
        {
            "capability": "protected-file mutation",
            "control_plane_status": "allowed_after_gate_policy_allows_specific_file_and_operation",
            "direct_chat_status": "forbidden",
            "required_gate": "protected file path + operation-specific approval + preflight diff + rollback/audit evidence",
            "why_not_direct": "Protected files define the OS itself; direct mutation would let an agent edit its own constitution.",
        },
        {
            "capability": "canonical knowledge promotion",
            "control_plane_status": "allowed_as_promotion_pipeline",
            "direct_chat_status": "forbidden",
            "required_gate": "source provenance + candidate artifact + review/decision record + promotion executor + graph/index audit",
            "why_not_direct": "Canonical truth must be promoted from evidence, not conversational assertion.",
        },
        {
            "capability": "external connector sends",
            "control_plane_status": "allowed_as_scoped_delivery_executor",
            "direct_chat_status": "forbidden",
            "required_gate": "destination allowlist + message preview + explicit send approval + delivery receipt/audit",
            "why_not_direct": "External sends can affect accounts, users, money, and reputation; they need destination-scoped approval and receipts.",
        },
        {
            "capability": "granting new runtime authority",
            "control_plane_status": "allowed_as_governance_change_proposal_and_gate_patch",
            "direct_chat_status": "forbidden",
            "required_gate": "authority-diff proposal + Gate/Permission-Matrix approval + manifest/policy patch + conformance tests",
            "why_not_direct": "No runtime may self-expand authority; ChaseOS can grant authority only through auditable governance mutation.",
        },
    ]


def _authority_catalog_text() -> str:
    rows = _control_plane_authority_catalog()
    lines = [
        "## ChaseOS main control-plane authority catalog",
        "",
        "ChaseOS should own these capabilities as the primary control plane. The key implementation rule is: Studio/Chat may expose the **control surface**, but target-side effects must run only through AOR/Gate-governed executors.",
        "",
        "Control-plane enforcement flags for this response:",
        "- action_catalog_visible: `true`",
        "- effects_performed_now: `false`",
        "- aor_governance_required: `true`",
        "- chaseos_gate_required_for_gated_effects: `true`",
        "- runtime_self_authorization_allowed: `false`",
        "",
    ]
    for idx, row in enumerate(rows, start=1):
        lines.extend(
            [
                f"### {idx}. {row['capability']}",
                f"- control_plane_status: `{row['control_plane_status']}`",
                f"- direct_chat_status: `{row['direct_chat_status']}`",
                f"- required_gate: {row['required_gate']}",
                f"- why_not_direct: {row['why_not_direct']}",
                "",
            ]
        )
    lines.extend(
        [
            "### What should not exist as a main-control-plane feature?",
            "None of the six requested capability classes are inherently invalid for ChaseOS. What is invalid is exposing them as ambient, direct, self-authorized chat effects. They belong in ChaseOS as gated action classes with schemas, approval evidence, exact-once consumption where needed, runtime-owned executors, and audit writeback.",
            "",
            "The action-envelope layer is now present for `/shell`, `/approve`, `/promote`, `/send`, `/grant-runtime-authority`, and `/protected-mutation`. These commands still create preview text only; the actual executors should be enabled one at a time with focused tests and live no-bypass proof.",
        ]
    )
    return "\n".join(lines)


def _audit_text() -> str:
    auth = _authority()
    flags = "\n".join(f"- {key}: `{str(value).lower()}`" for key, value in auth.items())
    return f"""## Audit-visible Studio Chat response format

Every bounded capability response should include:

- runtime: `Hermes`
- source_surface: `Studio Chat`
- route: `Agent Bus / runtime daemon`
- action_class: inspect | preview | handoff-preview | blocker-report
- generated_at: `{_utc_now()}`
- authority flags:
{flags}

This makes the Studio Chat lane useful without pretending it performed target-side effects."""


def _model_call_readiness_text(root: Path) -> str:
    from runtime.studio.provider_model_call_contract import (
        build_studio_provider_model_call_contract,
        format_studio_provider_model_call_contract,
    )

    return format_studio_provider_model_call_contract(
        build_studio_provider_model_call_contract(root)
    )


def _action_envelope_text(action: str, rest: str) -> str:
    payload = rest or "No payload supplied. Add the bounded request after the command."
    specs = {
        "shell": {
            "title": "shell/runtime execution",
            "intent_class": "shell_runtime_execution_preview",
            "required_executor": "AOR workflow manifest + runtime-owned executor",
            "extra": [
                "target_effect_allowed_now: `false`",
                "shell_command_performed_now: `false`",
                "agent_bus_task_created: `false`",
                "aor_workflow_manifest_required: `true`",
            ],
            "ladder": [
                "Create a scoped workflow/action packet with exact command, cwd, allowed tools, timeout, and expected output.",
                "Match the packet to an AOR workflow manifest and runtime-owned executor.",
                "Require operator/Gate approval when the requested scope crosses the runtime's existing authority ceiling.",
                "Execute only from the approved runtime daemon path and write post-run audit evidence.",
            ],
        },
        "approve": {
            "title": "approval consumption",
            "intent_class": "approval_consumption_preview",
            "required_executor": "Gate approval consumer + exact-once marker writer",
            "extra": [
                "approval_consumed_now: `false`",
                "exact_once_marker_required: `true`",
                "matching_fingerprint_required: `true`",
                "replay_refusal_required: `true`",
            ],
            "ladder": [
                "Extract the proposal/action fingerprint from the request.",
                "Find a matching durable operator/Gate approval artifact.",
                "Write an exact-once consumption marker before any target-side effect.",
                "Refuse replay or fingerprint mismatch and audit the refusal.",
            ],
        },
        "promote": {
            "title": "canonical knowledge promotion",
            "intent_class": "canonical_promotion_preview",
            "required_executor": "ChaseOS Gate promotion executor",
            "extra": [
                "canonical_mutation_performed_now: `false`",
                "chaseos_gate_required: `true`",
                "source_provenance_required: `true`",
                "promotion_audit_required: `true`",
            ],
            "ladder": [
                "Resolve source evidence, candidate artifact, target canonical path, and graph/index impact.",
                "Require Gate decision for the exact promotion operation.",
                "Apply promotion through the promotion executor, not Chat.",
                "Write graph/index/audit proof after mutation.",
            ],
        },
        "send": {
            "title": "external connector sends",
            "intent_class": "external_send_preview",
            "required_executor": "scoped delivery executor with receipt capture",
            "extra": [
                "external_delivery_performed_now: `false`",
                "destination_allowlist_required: `true`",
                "message_preview_required: `true`",
                "delivery_receipt_required: `true`",
            ],
            "ladder": [
                "Resolve destination, audience, account/connector, exact message, and attachments.",
                "Require destination allowlist and explicit send approval.",
                "Send only through the scoped connector executor.",
                "Record delivery receipt or blocker report.",
            ],
        },
        "grant-runtime-authority": {
            "title": "granting new runtime authority",
            "intent_class": "runtime_authority_grant_preview",
            "required_executor": "governance proposal + Gate-approved manifest/policy patch",
            "extra": [
                "runtime_self_authorization_allowed: `false`",
                "governance_patch_required: `true`",
                "permission_matrix_review_required: `true`",
                "conformance_tests_required: `true`",
            ],
            "ladder": [
                "Describe the requested authority delta, runtime, tools, paths, connectors, and trust-tier ceiling.",
                "Generate a governance diff/proposal for operator and Gate review.",
                "Patch manifests/policy only after approval through the protected governance path.",
                "Run conformance tests proving no ambient authority expansion.",
            ],
        },
        "protected-mutation": {
            "title": "protected-file mutation",
            "intent_class": "protected_file_mutation_preview",
            "required_executor": "Gate-approved protected-file mutation executor",
            "extra": [
                "protected_file_mutation_performed_now: `false`",
                "chaseos_gate_required: `true`",
                "preflight_diff_required: `true`",
                "rollback_plan_required: `true`",
            ],
            "ladder": [
                "Resolve exact protected path and operation.",
                "Prepare a preflight diff and rollback plan.",
                "Require Gate approval for that file and operation.",
                "Apply through the protected mutation executor and write audit evidence.",
            ],
        },
    }
    spec = specs[action]
    extra_lines = "\n".join(f"- {line}" for line in spec["extra"])
    ladder_lines = "\n".join(f"{idx}. {step}" for idx, step in enumerate(spec["ladder"], start=1))
    return f"""## Action envelope preview — {spec['title']}

requested_payload:
> {payload}

Envelope metadata:
- source_surface: `Studio Chat`
- runtime: `Hermes`
- route_for_effects: `Studio Chat → Agent Bus → runtime daemon → runtime-owned executor → Agent Bus result → audit writeback`
- intent_class: `{spec['intent_class']}`
- required_executor: `{spec['required_executor']}`
- preview_only: `true`
{extra_lines}

Execution ladder:
{ladder_lines}

Current result: preview only. No target-side effect was performed, no provider was called from Studio, and no Agent Bus task was created by this command."""


def _blockers_text() -> str:
    return """## Blocker report

Capabilities that remain intentionally blocked from direct Studio Chat execution:

- shell/runtime actions without an approved runtime-owned executor;
- approval consumption without exact proposal fingerprint matching;
- protected-file mutation and canonical promotion without ChaseOS Gate;
- external sends/connectors/account mutation without explicit scoped approval;
- new runtime authority grants without lower-phase governance updates;
- page-load or chat-open launch probes that spawn terminals.

Minimum proof needed to unlock any effectful action:
1. runtime-owned handler exists,
2. Agent Bus task schema is defined,
3. approval/Gate evidence is present where required,
4. focused tests prove no direct provider/terminal/page-load bypass,
5. audit writeback is generated after completion."""


def try_handle_studio_chat_capability(
    message: str,
    *,
    session_id: str = "",
    vault_root: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return a bounded Studio Chat capability packet, or None for normal chat."""
    action, rest = _extract_action(message)
    if action is None:
        return None
    root = Path(vault_root).resolve() if vault_root is not None else Path.cwd()
    if action in {"capabilities", "commands"}:
        text = _capabilities_text()
    elif action in {"status", "readiness"}:
        text = _readiness_text(root)
    elif action == "proposal":
        text = _proposal_text(rest)
    elif action == "handoff":
        text = _handoff_text(rest)
    elif action == "confirm":
        text = _confirm_text(rest)
    elif action == "authority":
        text = _authority_catalog_text()
    elif action == "model-call":
        text = _model_call_readiness_text(root)
    elif action in {"shell", "approve", "promote", "send", "grant-runtime-authority", "protected-mutation"}:
        text = _action_envelope_text(action, rest)
    elif action == "audit":
        text = _audit_text()
    elif action == "blockers":
        text = _blockers_text()
    else:
        return None
    return StudioChatCapabilityResult(
        ok=True,
        text=text,
        action=action,
        authority=_authority(),
    ).as_bridge_packet(session_id=session_id)
