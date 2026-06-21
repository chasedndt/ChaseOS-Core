"""
generator.py — SIC Phase 7 Pass 6 / Pass 6B
Output Generation Layer for the ChaseOS Source Intelligence Core.

Entry points:
    generate_output(evidence_result, task_spec, adapter=None) -> dict
        Pure generation — returns structured dict; no file I/O.

    generate_and_persist(workspace_id, evidence_result, task_spec, ...) -> dict
        Generation + workspace-local persistence in one step.
        Writes output to workspaces/{id}/outputs/{filename}.json.
        Records lightweight ref in workspace.json outputs[].

Architecture constraints (enforced):
  - generate_output() has NO side effects. Pure function over its inputs.
  - generate_and_persist() writes to workspace-local outputs/ ONLY.
  - No writes to 02_KNOWLEDGE/, 01_PROJECTS/, 00_HOME/, or any vault location.
  - promotion_candidate / vault_writeback_candidate is advisory — Gate writeback
    is human-gated and requires CHASEOS_PROMOTION_APPROVED=1.
  - idea_generation_draft outputs carry endorsement_status="unendorsed" by default.
  - Nothing in this module bypasses the ChaseOS Gate.
  - Provider seam is thin and provider-agnostic: StubGenerationAdapter
    (local-first) or any concrete provider adapter (e.g. AnthropicGenerationAdapter),
    each resolving its OWN credential via the runtime's model_config.yaml. No
    single provider or API key is required by this feature.

=== Canonical output type contract (Pass 6B) ===

Core 7 canonical types:
  source_summary, faq, briefing, study_guide,
  comparison_note, synthesis_draft, idea_generation_draft

Optional extras:
  timeline, qa_answer

Backward-compat aliases (resolved before processing):
  comparison      -> comparison_note
  idea_generation -> idea_generation_draft

=== CLI ===

  python -m runtime.source_intelligence.output.generator generate \\
    --workspace <id> --query <text> --output-type <type> [--top-k N] [--persist]

  python -m runtime.source_intelligence.output.generator list \\
    --workspace <id>

  python -m runtime.source_intelligence.output.generator inspect \\
    --workspace <id> --output-id <id-or-filename>

  python -m runtime.source_intelligence.output.generator list-types
"""

from __future__ import annotations

import abc
import argparse
import json
import os
import sys
from pathlib import Path

from .prompt_builder import (
    CANONICAL_OUTPUT_TYPES,
    OPTIONAL_EXTRA_OUTPUT_TYPES,
    OUTPUT_TYPE_ALIASES,
    VALID_OUTPUT_TYPES,
    build_citations,
    build_prompt,
    get_knowledge_class,
    get_min_evidence,
    is_non_canonical_by_default,
    is_vault_writeback_candidate,
    resolve_output_type,
)

# Retrieval import — used by CLI pipeline
from ..retrieval.retriever import query_workspace

# ── Generation status codes ────────────────────────────────────────────────────

_STATUS_OK          = "ok"               # real adapter; output generated
_STATUS_OK_STUB     = "ok-stub"          # stub adapter; no API call made
_STATUS_NO_EVIDENCE = "no-evidence"      # evidence_result had no packets
_STATUS_RTR_FAILED  = "retrieval-failed" # evidence_result had failure status
_STATUS_GEN_FAILED  = "generation-failed"  # adapter call failed or bad task_spec

_RETRIEVAL_OK_STATUSES = frozenset({"ok", "ok-partial", "ok-stale"})


# ── Generation adapter interface ───────────────────────────────────────────────


class GenerationAdapterBase(abc.ABC):
    """
    Abstract base class for SIC generation adapters.

    The adapter is a thin I/O layer. It receives a fully assembled prompt and
    returns generated text. It does not own workspace logic, output classification,
    writeback routing, or Gate enforcement — those belong to ChaseOS.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short provider identifier, e.g. 'anthropic', 'local_stub'."""
        ...

    @property
    @abc.abstractmethod
    def model_name(self) -> str:
        """Model identifier used for this adapter."""
        ...

    @abc.abstractmethod
    def generate(self, prompt: str, max_tokens: int = 2048) -> dict:
        """
        Generate text from a prompt.

        Returns:
            dict with keys:
                text        — the generated output text (str)
                model_used  — which model produced this (str)
                token_count — {"input": int, "output": int} or None
                error       — None on success; error message string on failure
        """
        ...


class GenerationAdapterError(Exception):
    """Raised when a generation adapter cannot be instantiated."""


# ── Stub adapter ───────────────────────────────────────────────────────────────


class StubGenerationAdapter(GenerationAdapterBase):
    """
    Local-first stub adapter — no API calls, no credentials required.

    Produces a clearly-labeled structured output showing the prompt that
    would be sent to a real generation provider. Useful for:
    - Testing the full generation + persistence pipeline without an API key
    - Verifying evidence packet assembly and citation structure
    - Offline / air-gapped development

    vault_writeback_candidate is always False for stub outputs because
    generated_text is a labeled placeholder, not real generated content.
    """

    @property
    def name(self) -> str:
        return "local_stub"

    @property
    def model_name(self) -> str:
        return "stub-generation-v1"

    def generate(self, prompt: str, max_tokens: int = 2048) -> dict:
        stub_text = (
            "[STUB OUTPUT -- local_stub adapter -- no API call was made]\n\n"
            "This output was produced by the StubGenerationAdapter. "
            "It is a placeholder -- not real generated content.\n\n"
            "To generate real output, configure a generation provider for your\n"
            "runtime (provider + credential resolved via model_config.yaml) and\n"
            "pass its provider_name to get_generation_adapter(); the matching\n"
            "provider adapter then activates. ChaseOS is provider-agnostic --\n"
            "no single provider or API key is required by this feature.\n\n"
            "--- PROMPT SENT TO ADAPTER (first 600 chars) ---\n"
            f"{prompt[:600]}{'...' if len(prompt) > 600 else ''}\n"
            "--- END PROMPT PREVIEW ---"
        )
        return {
            "text":        stub_text,
            "model_used":  self.model_name,
            "token_count": None,
            "error":       None,
        }


# ── Anthropic adapter ──────────────────────────────────────────────────────────


class AnthropicGenerationAdapter(GenerationAdapterBase):
    """
    Anthropic/Claude generation adapter.

    Requires:
    - anthropic package installed (pip install anthropic)
    - ANTHROPIC_API_KEY environment variable set

    Default model: claude-sonnet-4-6 (configurable via model_name parameter).
    """

    _DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model_name: str | None = None) -> None:
        self._model = model_name or self._DEFAULT_MODEL

        try:
            import anthropic as _mod
            self._anthropic_mod = _mod
        except ImportError:
            raise GenerationAdapterError(
                "anthropic package not installed. "
                "Run: .venv/Scripts/pip install anthropic"
            )

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise GenerationAdapterError(
                "ANTHROPIC_API_KEY environment variable not set. "
                "Set it or use the local_stub adapter."
            )

        self._client = self._anthropic_mod.Anthropic(api_key=api_key)

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, max_tokens: int = 2048) -> dict:
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = message.content[0].text if message.content else ""
            token_count = {
                "input":  message.usage.input_tokens,
                "output": message.usage.output_tokens,
            }
            return {
                "text":        text,
                "model_used":  self._model,
                "token_count": token_count,
                "error":       None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "text":        "",
                "model_used":  self._model,
                "token_count": None,
                "error":       str(exc),
            }


# ── Adapter factory ────────────────────────────────────────────────────────────


def get_generation_adapter(
    provider_name: str | None = None,
    model_name: str | None = None,
) -> GenerationAdapterBase:
    """
    Resolve and return a generation adapter. Never raises.

    Resolution order:
    1. provider_name == "local_stub"  -> StubGenerationAdapter
    2. provider_name == "anthropic"   -> AnthropicGenerationAdapter (or stub on fail)
    3. provider_name is None and ANTHROPIC_API_KEY set -> AnthropicGenerationAdapter
       (or stub on fail)
    4. All other cases -> StubGenerationAdapter

    Args:
        provider_name:  "anthropic", "local_stub", or None (auto-detect).
        model_name:     Model override for the chosen adapter.

    Returns:
        A GenerationAdapterBase instance — always.
    """
    if provider_name == "local_stub":
        return StubGenerationAdapter()

    wants_anthropic = provider_name == "anthropic" or (
        provider_name is None and bool(os.environ.get("ANTHROPIC_API_KEY"))
    )

    if wants_anthropic:
        try:
            return AnthropicGenerationAdapter(model_name=model_name)
        except GenerationAdapterError:
            pass

    return StubGenerationAdapter()


# ── Pure generation function ───────────────────────────────────────────────────


def generate_output(
    evidence_result: dict,
    task_spec: dict,
    adapter: GenerationAdapterBase | None = None,
    max_tokens: int = 2048,
) -> dict:
    """
    Generate a structured SIC output from evidence packets.

    Pure function — no file I/O, no side effects. Accepts the evidence_result
    dict from query_workspace(), constructs a generation prompt, calls the
    adapter, and returns a structured output dict with citations and a
    vault_writeback_candidate flag.

    To also persist the output to workspace-local storage, use
    generate_and_persist() instead.

    Alias resolution: output_type in task_spec is resolved before processing.
      comparison      -> comparison_note
      idea_generation -> idea_generation_draft

    This function never raises. All failure modes produce a result dict with
    a descriptive generation_status and populated errors/warnings lists.

    Args:
        evidence_result:  Output dict from query_workspace(). Must contain
                          evidence_packets and retrieval_status.
        task_spec:        Dict with keys:
                              output_type  — required; one of VALID_OUTPUT_TYPES
                              query_text   — optional; defaults to evidence_result
                                            query_text
                              instructions — optional; extra instructions for model
        adapter:          Optional pre-constructed GenerationAdapterBase.
                          If None, resolved via get_generation_adapter().
        max_tokens:       Maximum output tokens (default 2048).

    Returns:
        dict with keys:
            workspace_id             — from evidence_result
            query_text               — the effective query string
            output_type              — canonical output type (alias resolved)
            output_type_raw          — the raw output_type from task_spec
            knowledge_class          — ChaseOS taxonomy class for this output type
            endorsement_status       — "unendorsed" for idea_generation_draft; None otherwise
            vault_writeback_candidate — True if output meets promotion threshold
            writeback_path_hint      — "02_KNOWLEDGE/[Domain]/" if candidate; else None
            generated_text           — the actual output text
            evidence_packets         — evidence packets used (from retrieval)
            evidence_count           — number of evidence packets
            citations                — structured citation list (1-indexed)
            provider_name            — adapter provider name
            model_name               — adapter model name
            token_count              — {"input": int, "output": int} or None
            generation_status        — ok / ok-stub / no-evidence /
                                       retrieval-failed / generation-failed
            warnings                 — list of non-fatal warning strings
            errors                   — list of error strings
    """
    result = _empty_result(evidence_result, task_spec)

    # ── 1. Validate and resolve output_type ───────────────────────────────────
    output_type_raw = task_spec.get("output_type")
    if not output_type_raw:
        result["generation_status"] = _STATUS_GEN_FAILED
        result["errors"].append("task_spec must include 'output_type'.")
        return result

    if output_type_raw not in VALID_OUTPUT_TYPES:
        result["generation_status"] = _STATUS_GEN_FAILED
        result["errors"].append(
            f"Unknown output_type '{output_type_raw}'. "
            f"Canonical types: {sorted(CANONICAL_OUTPUT_TYPES)}. "
            f"Aliases: {OUTPUT_TYPE_ALIASES}. "
            f"Optional extras: {sorted(OPTIONAL_EXTRA_OUTPUT_TYPES)}."
        )
        return result

    output_type = resolve_output_type(output_type_raw)

    result["output_type"]     = output_type
    result["output_type_raw"] = output_type_raw
    result["knowledge_class"] = get_knowledge_class(output_type)

    if is_non_canonical_by_default(output_type):
        result["endorsement_status"] = "unendorsed"

    effective_query = (
        task_spec.get("query_text")
        or evidence_result.get("query_text")
        or ""
    )
    result["query_text"] = effective_query

    # ── 2. Check retrieval status ──────────────────────────────────────────────
    retrieval_status = evidence_result.get("retrieval_status", "unknown")

    if retrieval_status not in _RETRIEVAL_OK_STATUSES:
        result["generation_status"] = _STATUS_RTR_FAILED
        result["errors"].append(
            f"Retrieval status is '{retrieval_status}' — cannot generate output. "
            "Run query_workspace() successfully before calling generate_output()."
        )
        result["warnings"].extend(evidence_result.get("warnings", []))
        result["errors"].extend(evidence_result.get("errors", []))
        return result

    result["warnings"].extend(evidence_result.get("warnings", []))

    # ── 3. Extract evidence packets ────────────────────────────────────────────
    evidence_packets: list[dict] = evidence_result.get("evidence_packets", [])

    if not evidence_packets:
        result["generation_status"] = _STATUS_NO_EVIDENCE
        result["errors"].append(
            "No evidence packets in retrieval result. "
            "Index the workspace and query it before generating output."
        )
        return result

    result["evidence_packets"] = evidence_packets
    result["evidence_count"]   = len(evidence_packets)
    result["citations"]        = build_citations(evidence_packets)

    min_ev = get_min_evidence(output_type)
    if len(evidence_packets) < min_ev:
        result["warnings"].append(
            f"Output type '{output_type}' recommends at least {min_ev} evidence "
            f"packet(s), but only {len(evidence_packets)} available. "
            "Output quality may be limited."
        )

    # ── 4. Resolve adapter ─────────────────────────────────────────────────────
    if adapter is None:
        adapter = get_generation_adapter()

    result["provider_name"] = adapter.name
    result["model_name"]    = adapter.model_name

    # ── 5. Build prompt ────────────────────────────────────────────────────────
    # Pass the resolved output_type back through task_spec for prompt builder
    resolved_task_spec = dict(task_spec)
    resolved_task_spec["output_type"] = output_type
    prompt = build_prompt(evidence_result, resolved_task_spec)

    # ── 6. Generate ────────────────────────────────────────────────────────────
    gen = adapter.generate(prompt, max_tokens=max_tokens)

    if gen.get("error"):
        result["generation_status"] = _STATUS_GEN_FAILED
        result["errors"].append(f"Generation failed: {gen['error']}")
        return result

    result["generated_text"] = gen["text"]
    result["token_count"]    = gen.get("token_count")

    # ── 7. Set final status and vault_writeback_candidate ─────────────────────
    is_stub = isinstance(adapter, StubGenerationAdapter)
    result["generation_status"] = _STATUS_OK_STUB if is_stub else _STATUS_OK

    result["vault_writeback_candidate"] = is_vault_writeback_candidate(
        output_type=output_type,
        evidence_count=len(evidence_packets),
        generation_status=result["generation_status"],
    )

    if result["vault_writeback_candidate"]:
        result["writeback_path_hint"] = "02_KNOWLEDGE/[Domain]/"

    return result


# ── Generation + persistence ───────────────────────────────────────────────────


def generate_and_persist(
    workspace_id: str,
    evidence_result: dict,
    task_spec: dict,
    adapter: GenerationAdapterBase | None = None,
    max_tokens: int = 2048,
) -> dict:
    """
    Generate a SIC output and persist it to workspace-local storage.

    Calls generate_output() then output_store.save_output(). Returns the
    generation result augmented with persistence metadata.

    If generation fails, persistence is skipped.
    If persistence fails, generation result is returned with a warning.

    Args:
        workspace_id:     Workspace slug (directory name).
        evidence_result:  Output dict from query_workspace().
        task_spec:        Dict with output_type, query_text (optional), instructions (optional).
        adapter:          Optional pre-constructed GenerationAdapterBase.
        max_tokens:       Maximum output tokens (default 2048).

    Returns:
        The generate_output() result dict augmented with:
            persisted         — bool: True if output was written to disk
            persist_output_id — UUID of persisted output (or None)
            persist_path      — absolute path to persisted output file (or None)
            persist_filename  — filename of persisted output (or None)
            persist_error     — error message if persistence failed (or None)
    """
    # Import here to avoid circular import at module level
    from . import output_store

    # Run generation
    gen_result = generate_output(
        evidence_result=evidence_result,
        task_spec=task_spec,
        adapter=adapter,
        max_tokens=max_tokens,
    )

    # Augment result with persistence fields (defaults)
    gen_result["persisted"]         = False
    gen_result["persist_output_id"] = None
    gen_result["persist_path"]      = None
    gen_result["persist_filename"]  = None
    gen_result["persist_error"]     = None

    # Only persist if generation was usable
    if gen_result["generation_status"] not in (_STATUS_OK, _STATUS_OK_STUB):
        gen_result["persist_error"] = (
            f"Persistence skipped: generation_status='{gen_result['generation_status']}'"
        )
        return gen_result

    persist = output_store.save_output(
        workspace_id=workspace_id,
        generation_result=gen_result,
    )

    if persist["success"]:
        gen_result["persisted"]         = True
        gen_result["persist_output_id"] = persist["output_id"]
        gen_result["persist_path"]      = persist["output_path"]
        gen_result["persist_filename"]  = persist["output_filename"]
        if persist.get("error"):
            # save_output can return success=True with a non-fatal warning
            gen_result["persist_error"] = persist["error"]
            gen_result["warnings"].append(f"Persistence warning: {persist['error']}")
    else:
        gen_result["persist_error"] = persist.get("error")
        gen_result["warnings"].append(
            f"Output generated but could not be persisted: {persist.get('error')}"
        )

    return gen_result


# ── Internal helpers ───────────────────────────────────────────────────────────


def _empty_result(evidence_result: dict, task_spec: dict) -> dict:
    """Return a zero-state result dict."""
    return {
        "workspace_id":              evidence_result.get("workspace_id", "unknown"),
        "query_text":                (
            task_spec.get("query_text")
            or evidence_result.get("query_text", "")
        ),
        "output_type":               task_spec.get("output_type"),
        "output_type_raw":           task_spec.get("output_type"),
        "knowledge_class":           None,
        "endorsement_status":        None,
        "vault_writeback_candidate": False,
        "writeback_path_hint":       None,
        "generated_text":            "",
        "evidence_packets":          [],
        "evidence_count":            0,
        "citations":                 [],
        "provider_name":             None,
        "model_name":                None,
        "token_count":               None,
        "generation_status":         "unknown",
        "warnings":                  [],
        "errors":                    [],
    }


# ── CLI ────────────────────────────────────────────────────────────────────────


def _build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generator",
        description="SIC Phase 7 Pass 6B -- output generation from workspace evidence.",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # generate
    g = sub.add_parser(
        "generate",
        help="Query a workspace and generate a structured output",
    )
    g.add_argument("--workspace",   required=True, metavar="WORKSPACE_ID")
    g.add_argument("--query",       required=True, metavar="TEXT")
    g.add_argument("--output-type", required=True, metavar="TYPE",
                   help=f"Canonical types: {sorted(CANONICAL_OUTPUT_TYPES)}")
    g.add_argument("--top-k",       type=int, default=5, metavar="N")
    g.add_argument("--max-tokens",  type=int, default=2048, metavar="N")
    g.add_argument("--provider",    default=None, metavar="PROVIDER",
                   help="'anthropic' or 'local_stub' (default: auto)")
    g.add_argument("--instructions", default="", metavar="TEXT")
    g.add_argument("--persist",     action="store_true",
                   help="Persist output to workspace-local storage")
    g.add_argument("--json",        action="store_true",
                   help="Output full result as JSON")

    # list
    ls = sub.add_parser(
        "list",
        help="List stored outputs for a workspace",
    )
    ls.add_argument("--workspace", required=True, metavar="WORKSPACE_ID")
    ls.add_argument("--json",      action="store_true")

    # inspect
    ins = sub.add_parser(
        "inspect",
        help="Inspect a stored output by ID or filename",
    )
    ins.add_argument("--workspace",  required=True, metavar="WORKSPACE_ID")
    ins.add_argument("--output-id",  required=True, metavar="ID_OR_FILENAME")
    ins.add_argument("--full-text",  action="store_true",
                     help="Print full generated_text")
    ins.add_argument("--json",       action="store_true")

    # list-types
    sub.add_parser("list-types", help="List valid output types and knowledge classes")

    return p


def _print_generate_result(result: dict, as_json: bool = False) -> None:
    if as_json:
        out = {k: v for k, v in result.items() if k != "evidence_packets"}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    print()
    ws     = result["workspace_id"]
    status = result["generation_status"]
    print(f"Generation result -- workspace '{ws}'")
    print(f"  Status:            {status}")
    print(f"  Output type:       {result['output_type']}", end="")
    raw = result.get("output_type_raw")
    if raw and raw != result["output_type"]:
        print(f"  (alias for: {raw})", end="")
    print()
    print(f"  Knowledge class:   {result['knowledge_class']}")
    if result["endorsement_status"]:
        print(f"  Endorsement:       {result['endorsement_status']} [NON-CANONICAL]")
    print(f"  Provider:          {result['provider_name']}  |  Model: {result['model_name']}")
    print(f"  Evidence packets:  {result['evidence_count']}")
    print(f"  Vault candidate:   {result['vault_writeback_candidate']}")
    if result.get("writeback_path_hint"):
        print(f"  Writeback hint:    {result['writeback_path_hint']}")
    if result.get("token_count"):
        tc = result["token_count"]
        print(f"  Tokens:            in={tc.get('input')} out={tc.get('output')}")

    # Persistence fields
    if "persisted" in result:
        persisted = result["persisted"]
        print(f"  Persisted:         {persisted}")
        if persisted:
            print(f"  Output ID:         {result.get('persist_output_id')}")
            print(f"  Output file:       {result.get('persist_filename')}")
        if result.get("persist_error"):
            print(f"  Persist warning:   {result['persist_error']}")

    if result["warnings"]:
        print()
        for w in result["warnings"]:
            print(f"  [WARN] {w}")

    if result["errors"]:
        print()
        for e in result["errors"]:
            print(f"  [ERR]  {e}")

    if result["citations"]:
        print()
        print(f"  Citations ({len(result['citations'])}):")
        for c in result["citations"]:
            print(f"    [{c['citation_index']}] {c['source_title']}  "
                  f"(chunk {c['chunk_index']}, score={c['similarity_score']})")

    if result["generated_text"]:
        print()
        print("  Generated output:")
        print("  " + "-" * 60)
        for line in result["generated_text"].splitlines():
            print(f"  {line}")
        print("  " + "-" * 60)
    print()


def _print_list_result(result: dict, as_json: bool = False) -> None:
    from . import output_store as _store

    list_result = output_store.list_outputs(result["workspace_id"])
    if as_json:
        print(json.dumps(list_result, indent=2, ensure_ascii=False))
        return

    ws  = result["workspace_id"]
    outs = list_result.get("outputs", [])
    print()
    print(f"Outputs for workspace '{ws}' -- {len(outs)} total")
    if not outs:
        print("  (none)")
    for i, ref in enumerate(outs, 1):
        print()
        print(f"  [{i}] {ref.get('output_type')}  |  {ref.get('created_at', '')[:19]}")
        print(f"       ID:              {ref.get('output_id')}")
        print(f"       Status:          {ref.get('status')}  |  Gen: {ref.get('generation_status')}")
        print(f"       Knowledge class: {ref.get('suggested_knowledge_class')}")
        if ref.get("endorsement_status"):
            print(f"       Endorsement:     {ref['endorsement_status']} [NON-CANONICAL]")
        print(f"       Promotion cand.: {ref.get('promotion_candidate')}")
        print(f"       Evidence:        {ref.get('evidence_count')} packets")
        print(f"       Provider:        {ref.get('provider_name')}  |  {ref.get('model_name')}")
        print(f"       File:            {ref.get('output_filename')}")
        q = ref.get("query_text", "")
        if q:
            print(f"       Query:           {q[:80]}{'...' if len(q) > 80 else ''}")
    print()


def _print_inspect_result(
    workspace_id: str,
    output_id_or_filename: str,
    full_text: bool = False,
    as_json: bool = False,
) -> None:
    from . import output_store

    res = output_store.load_output(workspace_id, output_id_or_filename)

    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    if not res["success"]:
        print(f"\n  ERROR: {res['error']}")
        print()
        return

    out = res["output"]
    print()
    print(f"Output inspect -- workspace '{workspace_id}'")
    print(f"  Output ID:         {out.get('output_id')}")
    print(f"  Output type:       {out.get('output_type')}")
    print(f"  Knowledge class:   {out.get('suggested_knowledge_class')}")
    if out.get("endorsement_status"):
        print(f"  Endorsement:       {out['endorsement_status']} [NON-CANONICAL]")
    print(f"  Status:            {out.get('status')}")
    print(f"  Gen status:        {out.get('generation_status')}")
    print(f"  Promotion cand.:   {out.get('promotion_candidate')}")
    print(f"  Created:           {out.get('created_at', '')[:19]}")
    print(f"  Provider:          {out.get('provider_name')}  |  {out.get('model_name')}")
    print(f"  Evidence count:    {out.get('evidence_count')}")
    print(f"  Citations:         {len(out.get('citations', []))}")
    print(f"  File:              {res['output_path']}")

    if out.get("warnings"):
        print()
        for w in out["warnings"]:
            print(f"  [WARN] {w}")

    refs = out.get("evidence_packet_refs", [])
    if refs:
        print()
        print(f"  Evidence refs ({len(refs)}):")
        for i, r in enumerate(refs, 1):
            print(f"    [{i}] {r.get('source_title')}  "
                  f"score={r.get('similarity_score')}  "
                  f"chunk={r.get('chunk_index')}")

    if full_text and out.get("generated_text"):
        print()
        print("  Generated text:")
        print("  " + "-" * 60)
        for line in out["generated_text"].splitlines():
            print(f"  {line}")
        print("  " + "-" * 60)
    print()


def main() -> None:
    from . import output_store

    parser = _build_cli()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # ── list-types ─────────────────────────────────────────────────────────────
    if args.command == "list-types":
        from .prompt_builder import OUTPUT_TYPE_KNOWLEDGE_CLASS, OUTPUT_TYPE_ALIASES
        print()
        print("SIC canonical output types (core 7):")
        print()
        for otype in sorted(CANONICAL_OUTPUT_TYPES):
            klass = OUTPUT_TYPE_KNOWLEDGE_CLASS.get(otype, "?")
            print(f"  {otype:<25}  {klass}")
        print()
        print("Optional extras:")
        for otype in sorted(OPTIONAL_EXTRA_OUTPUT_TYPES):
            klass = OUTPUT_TYPE_KNOWLEDGE_CLASS.get(otype, "?")
            print(f"  {otype:<25}  {klass}")
        print()
        print("Backward-compat aliases:")
        for alias, canonical in sorted(OUTPUT_TYPE_ALIASES.items()):
            print(f"  {alias:<25}  -> {canonical}")
        print()
        sys.exit(0)

    # ── generate ───────────────────────────────────────────────────────────────
    if args.command == "generate":
        evidence_result = query_workspace(
            workspace_id=args.workspace,
            query_text=args.query,
            top_k=args.top_k,
        )

        task_spec = {
            "output_type":  args.output_type,
            "query_text":   args.query,
            "instructions": args.instructions,
        }

        adapter = get_generation_adapter(provider_name=args.provider)

        if args.persist:
            result = generate_and_persist(
                workspace_id=args.workspace,
                evidence_result=evidence_result,
                task_spec=task_spec,
                adapter=adapter,
                max_tokens=args.max_tokens,
            )
        else:
            result = generate_output(
                evidence_result=evidence_result,
                task_spec=task_spec,
                adapter=adapter,
                max_tokens=args.max_tokens,
            )
            result["workspace_id"] = args.workspace  # ensure workspace_id present

        _print_generate_result(result, as_json=args.json)
        ok = result["generation_status"] in (_STATUS_OK, _STATUS_OK_STUB)
        sys.exit(0 if ok else 1)

    # ── list ───────────────────────────────────────────────────────────────────
    if args.command == "list":
        list_result = output_store.list_outputs(args.workspace)

        if args.json:
            print(json.dumps(list_result, indent=2, ensure_ascii=False))
            sys.exit(0 if list_result["success"] else 1)

        outs = list_result.get("outputs", [])
        print()
        print(f"Outputs for workspace '{args.workspace}' -- {len(outs)} total")
        if not outs:
            print("  (none stored yet -- use --persist on generate)")
        for i, ref in enumerate(outs, 1):
            print()
            print(f"  [{i}] {ref.get('output_type')}  |  {ref.get('created_at', '')[:19]}")
            print(f"       ID:              {ref.get('output_id')}")
            print(f"       Status:          {ref.get('status')}  |  Gen: {ref.get('generation_status')}")
            print(f"       Knowledge class: {ref.get('suggested_knowledge_class')}")
            if ref.get("endorsement_status"):
                print(f"       Endorsement:     {ref['endorsement_status']} [NON-CANONICAL]")
            print(f"       Promotion cand.: {ref.get('promotion_candidate')}")
            print(f"       Evidence:        {ref.get('evidence_count')} packets")
            print(f"       File:            {ref.get('output_filename')}")
            q = ref.get("query_text", "")
            if q:
                print(f"       Query:           {q[:80]}{'...' if len(q) > 80 else ''}")
        print()
        sys.exit(0 if list_result["success"] else 1)

    # ── inspect ────────────────────────────────────────────────────────────────
    if args.command == "inspect":
        _print_inspect_result(
            workspace_id=args.workspace,
            output_id_or_filename=args.output_id,
            full_text=args.full_text,
            as_json=args.json,
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
