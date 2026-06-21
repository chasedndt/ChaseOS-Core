"""Governed feedback rules for Context Memory Core."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.common.evidence import now_utc


MEMORY_FEEDBACK_TYPES = {
    "confirm",
    "correct",
    "dismiss",
    "snooze",
    "mark_memory_candidate",
    "mark_personal_map_candidate",
    "thumbs_up",
    "thumbs_down",
    "show_more_like_this",
    "show_less_like_this",
    "never_show_this",
    "save",
    "delegate",
    "turn_into_task",
    "promote_to_memory",
    "link_to_project",
    "link_to_personal_map",
    "link_to_agent_brain",
}

FEEDBACK_RULE_TYPES = {
    "boost_topic",
    "suppress_topic",
    "boost_card_type",
    "suppress_card_type",
    "create_memory_candidate",
    "create_personal_map_candidate",
    "link_project_context",
    "link_agent_brain_context",
    "dismiss_pattern",
}

FEEDBACK_RULE_STATUSES = {"candidate", "active", "rejected", "superseded"}


@dataclass
class FeedbackRule:
    """Durable candidate rule inferred from Pulse or memory feedback."""

    rule_id: str
    rule_type: str
    target_type: str
    target: str
    scope: str = "user"
    weight_delta: float = 0.0
    condition: str = ""
    reason: str = ""
    source_card_id: str | None = None
    created_at: str = field(default_factory=now_utc)
    status: str = "candidate"
    canonical_writeback_allowed: bool = False

    def validate(self) -> None:
        if not self.rule_id:
            raise ValueError("rule_id is required")
        if self.rule_type not in FEEDBACK_RULE_TYPES:
            raise ValueError(f"rule_type must be one of {sorted(FEEDBACK_RULE_TYPES)}")
        if not self.target_type:
            raise ValueError("target_type is required")
        if not self.target:
            raise ValueError("target is required")
        if self.status not in FEEDBACK_RULE_STATUSES:
            raise ValueError(f"status must be one of {sorted(FEEDBACK_RULE_STATUSES)}")
        if self.canonical_writeback_allowed:
            raise ValueError("feedback rules cannot allow canonical writeback")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass
class FeedbackRuleResult:
    feedback_type: str
    creates_memory_candidate: bool
    creates_personal_map_candidate: bool
    requires_operator_review: bool
    canonical_writeback_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_feedback(feedback_type: str) -> FeedbackRuleResult:
    if feedback_type not in MEMORY_FEEDBACK_TYPES:
        raise ValueError(f"feedback_type must be one of {sorted(MEMORY_FEEDBACK_TYPES)}")
    return FeedbackRuleResult(
        feedback_type=feedback_type,
        creates_memory_candidate=feedback_type in {"mark_memory_candidate", "promote_to_memory"},
        creates_personal_map_candidate=feedback_type in {
            "mark_personal_map_candidate",
            "link_to_personal_map",
        },
        requires_operator_review=feedback_type in {
            "correct",
            "mark_memory_candidate",
            "mark_personal_map_candidate",
            "promote_to_memory",
            "link_to_project",
            "link_to_personal_map",
            "link_to_agent_brain",
            "turn_into_task",
        },
        canonical_writeback_allowed=False,
    )
