"""
perplexity_connector.py — ChaseOS Phase 8 Pass 8
Perplexity API connector for the Connector / Capture layer.

Accepts an operator-supplied query, calls the Perplexity API, normalizes the
returned answer into a ContentPacket, and returns it ready for capture_content().
No vault I/O here.

DEFAULT INPUT CLASS: 'digest'
    Perplexity outputs are research synthesis/digest-style artifacts — a single
    curated AI-backed answer aggregated from multiple web sources. They map to
    'digest' (quarantine: Digests/, knowledge_class: source-derived).

    'source' applies to discrete single-origin external articles.
    'digest' applies when one artifact synthesizes multiple sources — which is
    exactly what a Perplexity answer is. This is the justified default.

    The operator may override with --class for edge cases.

DEFAULT SOURCE PLATFORM: 'perplexity'
    Hard-coded to 'perplexity'. No override needed unless multi-provider use
    is added in a future pass.

CREDENTIAL HANDLING:
    API key is loaded from the environment variable PERPLEXITY_API_KEY only.
    Never read from files, never hardcoded, never written to sidecars or logs.

    Required env var: PERPLEXITY_API_KEY
    Missing key behaviour: raises PerplexityCredentialError immediately, before
    any network call is made. The error message tells the operator which variable
    to set.

    The API key is NEVER written to:
        - ContentPacket fields
        - sidecar metadata (.meta.json)
        - extra_metadata
        - build logs or archive notes

DEFAULT MODEL: 'sonar'
    Perplexity's standard web-search-augmented model. This is the correct
    default for research digest capture — it returns grounded answers with
    citations. The operator may override with --model.

    Other usable models at time of writing:
        sonar-pro          — higher-quality, higher cost
        sonar-reasoning    — chain-of-thought, longer responses

CAPTURE METHOD: 'api'
    capture_method field is 'api' for all API-backed connectors.

ORIGIN KIND DEFAULT: 'ai-generated'
    Perplexity outputs are AI-generated syntheses. The origin_kind field is
    set to 'ai-generated' by default. The operator may override via --origin-kind.

TITLE DERIVATION:
    If --title is not provided, the title is derived from the query:
        - Truncated to 80 characters at a word boundary
        - Used as the sidecar title and filename slug basis
    The operator may override with an explicit --title.

PROVENANCE FIELDS:
    Top-level ContentPacket fields:
        title           — derived from query or --title override
        input_class     — 'digest' (default) or --class override
        source_platform — 'perplexity'
        capture_method  — 'api'
        origin_kind     — 'ai-generated' (default) or --origin-kind override
        captured_at     — ISO 8601 UTC timestamp of when the capture ran

    extra_metadata fields (stored in sidecar .meta.json):
        query               — the exact query string sent to the API
        model               — model name as returned by the API
        citations           — list of citation URLs if returned by the API
        citation_count      — integer count of citations
        response_id         — Perplexity response ID if present
        usage               — token usage dict if returned (prompt/completion/total)
        capture_method_detail — 'perplexity-api-chat-completions'

    NOT stored anywhere:
        - API key
        - Authorization headers
        - Raw HTTP response objects

CITATIONS:
    Perplexity may return a top-level 'citations' field (list of URL strings)
    alongside the standard OpenAI-compatible choices structure.
    If present, citations are extracted and stored in extra_metadata.
    If absent, citations = [] (empty list, citation_count = 0).

DEDUP:
    Standard Pass 6 SHA-256 dedup registry applies.
    Dedup key = SHA-256 of the returned answer text (content body).
    Same answer returned for the same query on repeat calls → duplicate on
    second capture attempt. The operator gets an explicit duplicate result.
    No duplicate file is written.

QUARANTINE DOCTRINE:
    All captures land in 03_INPUTS/00_QUARANTINE/[class]/ (default: Digests/).
    NOT ingested into SIC at capture time.
    Pipeline: query -> API response -> ContentPacket -> capture_content() -> quarantine.
    No auto-promotion. No SIC trigger. Promotion remains explicit and governed.

API NOTES:
    Endpoint: POST https://api.perplexity.ai/chat/completions
    Auth: Bearer token from PERPLEXITY_API_KEY env var
    Protocol: OpenAI-compatible chat completions format
    Extensions: 'citations' field in response (Perplexity-specific)
    Stdlib only: urllib.request + json (no external HTTP dependencies)
    Timeout: 60 seconds per request

HONEST LIMITATIONS:
    - One-shot query only — no multi-turn conversation support in this pass
    - No automatic scheduled polling or batch queries
    - No Grok integration (future pass)
    - No watched-folder automation
    - No promotion into knowledge — quarantine only
    - No SIC auto-ingestion
    - No deep citation content fetching (URLs stored, content not fetched)
    - No background jobs or retries beyond the single request
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ..content_packet import ContentPacket, INPUT_CLASS_DIGEST


# ── Constants ──────────────────────────────────────────────────────────────────

_ENV_KEY               = "PERPLEXITY_API_KEY"
_API_URL               = "https://api.perplexity.ai/chat/completions"
_DEFAULT_MODEL         = "sonar"
_DEFAULT_SOURCE_PLATFORM = "perplexity"
_DEFAULT_INPUT_CLASS   = INPUT_CLASS_DIGEST
_TITLE_MAX_LEN         = 80
_REQUEST_TIMEOUT       = 60  # seconds


# ── Public exception types ─────────────────────────────────────────────────────

class PerplexityCredentialError(Exception):
    """
    Raised when PERPLEXITY_API_KEY is missing or empty.

    This is raised before any network call is attempted.
    The operator should set the PERPLEXITY_API_KEY environment variable.
    """


class PerplexityAPIError(Exception):
    """
    Raised when the Perplexity API call fails.

    Covers:
        - HTTP errors (4xx, 5xx)
        - Network/connection failures
        - Non-JSON response bodies
        - Empty choices in a successful response
    """


# ── Credential loading ─────────────────────────────────────────────────────────

def _get_api_key() -> str:
    """
    Load the Perplexity API key from the environment.

    Reads PERPLEXITY_API_KEY. Raises PerplexityCredentialError if missing
    or empty. Never reads from files, never hardcodes a default.
    """
    key = os.environ.get(_ENV_KEY, "").strip()
    if not key:
        raise PerplexityCredentialError(
            f"Perplexity API key not found. "
            f"Set the environment variable {_ENV_KEY!r} before capturing. "
            f"Example: set {_ENV_KEY}=pplx-... (Windows) or export {_ENV_KEY}=pplx-... (Unix)"
        )
    return key


# ── API call ───────────────────────────────────────────────────────────────────

def query_perplexity(
    query: str,
    *,
    model: str = _DEFAULT_MODEL,
    api_key: str | None = None,
    system: str | None = None,
) -> dict:
    """
    Call the Perplexity API with a single user query.

    Args:
        query:    The research question or prompt to send.
        model:    Perplexity model name (default: 'sonar').
        api_key:  API key string. If None, loads from PERPLEXITY_API_KEY env var.
        system:   Optional system prompt prepended to the messages list.
                  Keep this minimal — this connector is not a prompt-engineering layer.

    Returns:
        Parsed JSON response dict from the Perplexity API.

    Raises:
        PerplexityCredentialError: if api_key is None and env var is missing.
        PerplexityAPIError: on HTTP errors, network failures, or invalid JSON response.

    The API key is only used in the Authorization header. It is NOT stored in
    the returned dict, the ContentPacket, or any ChaseOS file.
    """
    if api_key is None:
        api_key = _get_api_key()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": query})

    payload = json.dumps({
        "model":    model,
        "messages": messages,
    }).encode("utf-8")

    req = urllib.request.Request(
        _API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = "(unreadable)"
        raise PerplexityAPIError(
            f"Perplexity API returned HTTP {exc.code}: {err_body[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise PerplexityAPIError(
            f"Perplexity API network error: {exc.reason}"
        ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise PerplexityAPIError(
            f"Perplexity API returned non-JSON response: {body[:200]!r}"
        ) from exc


# ── Response normalization ─────────────────────────────────────────────────────

def _extract_content(response: dict) -> str:
    """
    Extract the answer text from a Perplexity API response.

    Expects OpenAI-compatible chat completions format:
        response['choices'][0]['message']['content']

    Raises PerplexityAPIError if choices are absent or empty.
    Returns empty string if message content is an empty string.
    """
    choices = response.get("choices", [])
    if not choices:
        raise PerplexityAPIError(
            "Perplexity API returned no choices in response. "
            f"Full response keys: {list(response.keys())}"
        )
    return choices[0].get("message", {}).get("content", "")


def _extract_citations(response: dict) -> list[str]:
    """
    Extract citation URLs from a Perplexity API response.

    The 'citations' field is a Perplexity-specific extension to the OpenAI
    chat completions format. It contains a list of URL strings sourced by
    the model during web search.

    Returns an empty list if no citations field is present.
    """
    raw = response.get("citations", [])
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, str)]
    return []


def _make_title(query: str) -> str:
    """
    Derive a sidecar title from the query text.

    Truncates at _TITLE_MAX_LEN characters. If truncation is needed, attempts
    to break at the last space before the cutoff to avoid mid-word cuts.
    Appends '...' to indicate truncation.
    """
    query = query.strip()
    if len(query) <= _TITLE_MAX_LEN:
        return query

    # Break at last space before cutoff to avoid mid-word cuts
    cut = query[:_TITLE_MAX_LEN]
    last_space = cut.rfind(" ")
    if last_space > _TITLE_MAX_LEN // 2:
        cut = cut[:last_space]
    return cut.rstrip() + "..."


# ── Public connector API ───────────────────────────────────────────────────────

def capture_from_perplexity(
    *,
    query: str,
    model: str = _DEFAULT_MODEL,
    api_key: str | None = None,
    system: str | None = None,
    title: str | None = None,
    input_class: str = _DEFAULT_INPUT_CLASS,
    source_platform: str = _DEFAULT_SOURCE_PLATFORM,
    workspace_hint: str | None = None,
    domain_hint: str | None = None,
    project_hint: str | None = None,
    topic_hint: str | None = None,
    event_date_hint: str | None = None,
    origin_kind: str | None = None,
    desired_output_kind: str | None = None,
) -> ContentPacket:
    """
    Query the Perplexity API and return a ContentPacket ready for capture_content().

    This is the single public entry point for this connector.
    It handles credential loading, the API call, response normalization,
    and ContentPacket assembly in one step.

    Args:
        query:               Research question or prompt. Required. Must not be empty.
        model:               Perplexity model to use (default: 'sonar').
        api_key:             API key string. If None, loads from PERPLEXITY_API_KEY.
        system:              Optional system prompt (minimal use only).
        title:               Override title. If None, derived from query text.
        input_class:         ContentPacket class (default: 'digest').
        source_platform:     Source platform slug (default: 'perplexity').
        workspace_hint:      SIC workspace hint for future ingestion. Hint only.
        domain_hint:         ChaseOS domain hint. Hint only.
        project_hint:        Active project hint. Hint only.
        topic_hint:          Subject label hint. Hint only.
        event_date_hint:     ISO 8601 date hint (YYYY-MM-DD). Hint only.
        origin_kind:         Content authorship origin. Defaults to 'ai-generated'.
        desired_output_kind: Intended output type. Hint only.

    Returns:
        ContentPacket with the Perplexity answer as content and provenance fields
        populated. The packet has NOT been written to quarantine — call
        capture_content(packet, vault_root) to complete the intake.

    Raises:
        ValueError:                 if query is empty.
        PerplexityCredentialError:  if API key is missing (before network call).
        PerplexityAPIError:         if the API call fails.

    QUARANTINE DOCTRINE:
        This function does not write to vault. SIC is not triggered. No promotion.
        All of that happens in capture_content() and the Gate, after operator review.
    """
    if not query or not query.strip():
        raise ValueError("query must not be empty.")

    response = query_perplexity(query, model=model, api_key=api_key, system=system)

    content = _extract_content(response)
    if not content.strip():
        content = f"[Perplexity returned empty content for query: {query!r}]"

    citations        = _extract_citations(response)
    response_id      = response.get("id")
    usage            = response.get("usage", {})
    model_used       = response.get("model", model)

    resolved_title   = title if title else _make_title(query)
    captured_at      = datetime.now(timezone.utc).isoformat()

    extra_metadata = {
        "query":                  query,
        "model":                  model_used,
        "citations":              citations,
        "citation_count":         len(citations),
        "response_id":            response_id,
        "usage":                  usage,
        "capture_method_detail":  "perplexity-api-chat-completions",
    }

    return ContentPacket(
        content=content,
        input_class=input_class,
        source_platform=source_platform,
        title=resolved_title,
        captured_at=captured_at,
        origin_kind=origin_kind if origin_kind is not None else "ai-generated",
        capture_method="api",
        extra_metadata=extra_metadata,
        workspace_hint=workspace_hint,
        domain_hint=domain_hint,
        project_hint=project_hint,
        topic_hint=topic_hint,
        event_date_hint=event_date_hint,
        desired_output_kind=desired_output_kind,
    )
