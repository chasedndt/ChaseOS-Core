"""
advisory.py — ChaseOS Graph Substrate: AOR Advisory Narrowing Seam (Pass 2)

Provides graph-informed candidate read suggestions for AOR Stage 5 (required_reads).

This is an advisory layer only. It does not replace AOR Stage 5, does not
override role card permission ceilings, and does not write to the vault.
Its output is a suggestion list that the AOR engine may incorporate into
its required_reads heuristic if the operator or a future Stage 5 integration
chooses to do so.

Design intent:
  AOR Stage 5 currently derives required_reads from the workflow manifest's
  declared list (static). The advisory seam adds a dynamic, graph-proximity
  signal: "given the task context and workflow ID, which files does the graph
  substrate think are likely relevant?"

  The integration boundary is explicitly advisory:
    AOR stage 5 may read advisory.candidate_reads as *suggestions*.
    It must still apply its permission ceiling and manifest constraints.
    Nothing in this module bypasses the Gate or any role card restriction.

Public API:
  advise_required_reads(query_service, task_context, workflow_id)
      → AdvisoryNarrowingResult

  AdvisoryNarrowingResult.candidate_reads   — ranked list of vault-relative paths
  AdvisoryNarrowingResult.confidence        — "graph-advisory" always
  AdvisoryNarrowingResult.reasoning         — human-readable explanation of basis
  AdvisoryNarrowingResult.graph_terms_used  — terms extracted from task context
  AdvisoryNarrowingResult.is_empty          — True if graph found nothing relevant

Integration example (AOR engine, Stage 5):
  from runtime.graph.advisory import advise_required_reads

  advisory = advise_required_reads(qs, task_context=task.task_type, workflow_id=manifest.id)
  candidate_reads = advisory.candidate_reads  # use as hints, not as authority
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .query import GraphQueryService


# ── Result model ─────────────────────────────────────────────────────────────

@dataclass
class AdvisoryNarrowingResult:
    """
    Advisory output from graph-proximity narrowing.

    This is NOT an authoritative required_reads list.
    It is a graph-informed suggestion that AOR Stage 5 may incorporate.

    All fields are read-only after construction.
    """
    workflow_id: str
    task_context: str
    graph_terms_used: list[str]
    candidate_reads: list[str]          # vault-relative paths, ranked by graph relevance
    candidate_node_ids: list[str]       # matching node_ids (for debugging/audit)
    reasoning: str                      # human-readable explanation
    confidence: str = "graph-advisory"  # always this value; not a Confidence constant

    @property
    def is_empty(self) -> bool:
        return len(self.candidate_reads) == 0

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "task_context": self.task_context,
            "graph_terms_used": self.graph_terms_used,
            "candidate_reads": self.candidate_reads,
            "candidate_node_ids": self.candidate_node_ids,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "is_empty": self.is_empty,
        }


# ── Term extraction ───────────────────────────────────────────────────────────

# Stopwords to skip when tokenizing task context into graph search terms
_STOPWORDS = frozenset([
    "a", "an", "the", "and", "or", "of", "in", "to", "for", "on",
    "at", "by", "with", "from", "this", "that", "is", "are", "was",
    "be", "do", "run", "get", "set", "use", "via", "the", "all",
])

_MIN_TERM_LEN = 3
_MAX_TERMS = 8


def _extract_terms(text: str, workflow_id: str) -> list[str]:
    """
    Extract graph search terms from task context and workflow ID.

    Strategy:
    1. Tokenize task_context into words (split on non-alphanumeric)
    2. Filter out stopwords and short tokens
    3. Add workflow_id components (split on underscores and dots)
    4. Deduplicate, preserve order, cap at _MAX_TERMS
    """
    terms: list[str] = []
    seen: set[str] = set()

    def add(tok: str) -> None:
        t = tok.lower().strip()
        if len(t) >= _MIN_TERM_LEN and t not in _STOPWORDS and t not in seen:
            seen.add(t)
            terms.append(t)

    # From task context
    for tok in re.split(r"[^a-zA-Z0-9]+", text):
        add(tok)

    # From workflow_id (e.g. "operator_today" → "operator", "today")
    for tok in re.split(r"[._\-]+", workflow_id):
        add(tok)

    return terms[:_MAX_TERMS]


# ── Core advisory function ────────────────────────────────────────────────────

def advise_required_reads(
    query_service: GraphQueryService,
    task_context: str,
    workflow_id: str,
    *,
    max_candidates: int = 10,
    include_community_neighbors: bool = True,
) -> AdvisoryNarrowingResult:
    """
    Produce a graph-advisory narrowing result for an AOR task.

    Parameters
    ----------
    query_service : GraphQueryService
        Live query service over the current GraphSnapshot.
    task_context : str
        Natural-language task description or task_type string from the AOR manifest.
        Used as the primary source of search terms.
    workflow_id : str
        The AOR workflow manifest ID (e.g. "operator_today").
        Components (split on underscores) are added to the search term set.
    max_candidates : int
        Maximum candidate paths to return. Default 10.
    include_community_neighbors : bool
        Whether to expand matches to community neighbors. Default True.
        Disable for tighter/faster narrowing.

    Returns
    -------
    AdvisoryNarrowingResult with ranked candidate_reads list.

    Notes
    -----
    - Always returns a result; never raises.
    - If no graph matches are found, returns an empty result with is_empty=True.
    - The confidence field is always "graph-advisory" — not a Confidence constant.
    - The result is purely advisory: AOR Stage 5 may ignore it entirely.
    """
    terms = _extract_terms(task_context, workflow_id)

    if not terms:
        return AdvisoryNarrowingResult(
            workflow_id=workflow_id,
            task_context=task_context,
            graph_terms_used=[],
            candidate_reads=[],
            candidate_node_ids=[],
            reasoning="No usable search terms could be extracted from task context.",
        )

    # Graph narrowing via query service
    try:
        narrowing = query_service.narrow_to_relevant(
            terms,
            include_community_neighbors=include_community_neighbors,
            max_source_files=max_candidates,
        )
    except Exception as exc:
        # Fail-open: advisory failures must never block AOR execution
        return AdvisoryNarrowingResult(
            workflow_id=workflow_id,
            task_context=task_context,
            graph_terms_used=terms,
            candidate_reads=[],
            candidate_node_ids=[],
            reasoning=f"Graph narrowing failed (advisory only): {exc}",
        )

    candidate_reads = narrowing.get("source_files", [])[:max_candidates]
    candidate_node_ids = narrowing.get("matching_node_ids", [])

    # Build reasoning string
    if candidate_reads:
        top_display = ", ".join(f"`{p}`" for p in candidate_reads[:3])
        more = f" (+{len(candidate_reads) - 3} more)" if len(candidate_reads) > 3 else ""
        reasoning = (
            f"Graph substrate matched terms {terms} to {len(candidate_reads)} source file(s) "
            f"via label proximity and community expansion: {top_display}{more}. "
            f"Advisory only — AOR Stage 5 applies permission ceiling and manifest constraints."
        )
    else:
        reasoning = (
            f"Graph substrate found no files closely matching terms {terms}. "
            f"Fallback to manifest-declared required_reads recommended."
        )

    return AdvisoryNarrowingResult(
        workflow_id=workflow_id,
        task_context=task_context,
        graph_terms_used=terms,
        candidate_reads=candidate_reads,
        candidate_node_ids=candidate_node_ids,
        reasoning=reasoning,
    )
