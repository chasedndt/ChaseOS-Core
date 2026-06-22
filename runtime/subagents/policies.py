"""Policy helpers for sub-agent tool and memory boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SubAgentMemoryPolicy, SubAgentPreset, SubAgentToolPolicy


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    reason: str
    target: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def approval_required(self) -> bool:
        return self.decision == "approval_required"


def _matches(patterns: tuple[str, ...], target: str) -> bool:
    return target in patterns or "*" in patterns


def evaluate_tool_request(preset: SubAgentPreset | SubAgentToolPolicy, tool_name: str) -> PolicyDecision:
    policy = preset.tools if isinstance(preset, SubAgentPreset) else preset
    if _matches(policy.denied, tool_name):
        return PolicyDecision("deny", f"tool is denied for this sub-agent: {tool_name}", tool_name)
    if _matches(policy.requires_approval, tool_name):
        return PolicyDecision(
            "approval_required",
            f"tool requires ChaseOS approval before use: {tool_name}",
            tool_name,
        )
    if _matches(policy.allowed, tool_name):
        return PolicyDecision("allow", f"tool is allowed by preset policy: {tool_name}", tool_name)
    return PolicyDecision("deny", f"tool is not listed in allowed tools: {tool_name}", tool_name)


def evaluate_memory_read(preset: SubAgentPreset | SubAgentMemoryPolicy, target: str) -> PolicyDecision:
    policy = preset.memory if isinstance(preset, SubAgentPreset) else preset
    if _matches(policy.denied, target):
        return PolicyDecision("deny", f"memory read target is denied: {target}", target)
    if _matches(policy.read, target):
        return PolicyDecision("allow", f"memory read target is allowed: {target}", target)
    return PolicyDecision("deny", f"memory read target is not listed in allowed reads: {target}", target)


def evaluate_memory_write(preset: SubAgentPreset | SubAgentMemoryPolicy, target: str) -> PolicyDecision:
    policy = preset.memory if isinstance(preset, SubAgentPreset) else preset
    if _matches(policy.denied, target):
        return PolicyDecision("deny", f"memory write target is denied: {target}", target)
    if _matches(policy.write, target):
        return PolicyDecision("approval_required", f"memory write target requires review: {target}", target)
    return PolicyDecision("deny", f"memory write target is not listed in reviewable writes: {target}", target)
