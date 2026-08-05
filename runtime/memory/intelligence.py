"""Local-only ChaseOS memory intelligence harness v0.1.

This module intentionally provides deterministic, lexical memory-candidate
analysis only. It does not call providers, create embeddings, mutate Hermes
memory, promote canonical truth, or reach the network.
"""

from __future__ import annotations

import json
import re
import string
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable

CATEGORIES: tuple[str, ...] = (
    "preference",
    "project",
    "profile",
    "goal",
    "entity",
    "event",
    "fact",
    "request",
    "general",
)

AUTHORITY_FLAGS: dict[str, bool] = {
    "network_call_performed": False,
    "provider_call_performed": False,
    "hermes_memory_mutated": False,
    "runtime_memory_provider_switched": False,
    "canonical_truth_promoted": False,
    "external_source_used": False,
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "is",
    "it",
    "of",
    "on",
    "the",
    "to",
    "uses",
    "use",
    "with",
}

_ENTITY_CLAIM_RE = re.compile(
    r"^(?P<entity>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)\s+"
    r"(?P<attribute>[a-z][\w-]{1,40})\s+"
    r"(?:is|=)\s+"
    r"(?P<value>.+?)\.?$"
)


@dataclass(frozen=True)
class MemoryCandidate:
    """A local, advisory analysis result for one memory candidate."""

    id: str
    text: str
    category: str
    entity: str | None = None
    attribute: str | None = None
    value: str | None = None
    is_near_duplicate: bool = False
    duplicate_of: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    relevance_score: int = 0


def classify_candidate(text: str) -> str:
    """Classify a memory candidate into the v0.1 category set."""

    stripped = text.strip()
    lowered = stripped.lower()

    if re.search(r"\b(i|we|user)\s+(prefer|likes?|want|wants)\b", lowered):
        return "preference"
    if lowered.startswith("project ") or " project " in f" {lowered} ":
        return "project"
    if lowered.startswith("profile:") or lowered.startswith("user profile:"):
        return "profile"
    if lowered.startswith("goal:") or lowered.startswith("objective:"):
        return "goal"
    if lowered.startswith("fact:"):
        return "fact"
    if lowered.startswith("please ") or " remember " in f" {lowered} ":
        return "request"
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", stripped) or lowered.startswith(("today ", "yesterday ", "on ")):
        return "event"
    if _parse_entity_claim(stripped) is not None:
        return "entity"
    return "general"


def analyze_candidates(texts: Iterable[str]) -> list[MemoryCandidate]:
    """Analyze candidates for category, duplicate, and supersession signals."""

    candidates: list[MemoryCandidate] = []
    seen_tokens: list[tuple[str, set[str]]] = []
    latest_claim_by_key: dict[tuple[str, str], int] = {}

    for index, text in enumerate(texts, start=1):
        candidate_id = f"candidate_{index}"
        stripped = text.strip()
        category = classify_candidate(stripped)
        parsed = _parse_entity_claim(stripped) if category == "entity" else None
        tokens = set(_tokens(stripped))

        duplicate_of = None
        for previous_id, previous_tokens in seen_tokens:
            if _jaccard(tokens, previous_tokens) >= 0.75:
                duplicate_of = previous_id
                break

        candidate = MemoryCandidate(
            id=candidate_id,
            text=stripped,
            category=category,
            entity=parsed[0] if parsed else None,
            attribute=parsed[1] if parsed else None,
            value=parsed[2] if parsed else None,
            is_near_duplicate=duplicate_of is not None,
            duplicate_of=duplicate_of,
        )
        candidates.append(candidate)
        seen_tokens.append((candidate_id, tokens))

        if candidate.entity and candidate.attribute:
            key = (candidate.entity.lower(), candidate.attribute.lower())
            previous_index = latest_claim_by_key.get(key)
            if previous_index is not None:
                previous = candidates[previous_index]
                candidates[previous_index] = replace(previous, superseded_by=candidate.id)
                candidates[-1] = replace(candidate, supersedes=previous.id)
            latest_claim_by_key[key] = len(candidates) - 1

    return candidates


def retrieve_relevant(
    candidates: Iterable[MemoryCandidate],
    query: str,
    *,
    limit: int = 5,
) -> list[MemoryCandidate]:
    """Return deterministic keyword-relevance matches for a query."""

    query_terms = set(_tokens(query))
    if not query_terms or limit <= 0:
        return []

    scored: list[tuple[int, int, MemoryCandidate]] = []
    for index, candidate in enumerate(candidates):
        score = len(query_terms.intersection(_tokens(candidate.text)))
        if score > 0:
            scored.append((score, index, replace(candidate, relevance_score=score)))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [candidate for _, _, candidate in scored[:limit]]


def emit_eval_artifact(candidates: Iterable[MemoryCandidate], output_path: str | Path) -> dict[str, object]:
    """Write a deterministic v0.1 eval artifact for local proof review."""

    candidate_list = list(candidates)
    category_counts = Counter(candidate.category for candidate in candidate_list)
    payload: dict[str, object] = {
        "schema_version": "memory-intelligence-eval.v0.1",
        "counts": {
            "total_candidates": len(candidate_list),
            "near_duplicates": sum(1 for candidate in candidate_list if candidate.is_near_duplicate),
            "superseded_claims": sum(1 for candidate in candidate_list if candidate.superseded_by),
            "categories": {key: category_counts[key] for key in sorted(category_counts)},
        },
        "authority_flags": dict(AUTHORITY_FLAGS),
        "candidates": [asdict(candidate) for candidate in candidate_list],
    }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _parse_entity_claim(text: str) -> tuple[str, str, str] | None:
    match = _ENTITY_CLAIM_RE.match(text.strip())
    if not match:
        return None
    return (
        match.group("entity").strip(),
        match.group("attribute").strip(),
        match.group("value").strip().rstrip("."),
    )


def _tokens(text: str) -> list[str]:
    lowered = text.lower().translate(str.maketrans("", "", string.punctuation))
    return [_stem(token) for token in lowered.split() if token and token not in _STOPWORDS]


def _stem(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / len(left.union(right))
