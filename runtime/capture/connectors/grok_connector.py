"""
grok_connector.py — ChaseOS Phase 8 Pass 10
Grok/xAI API connector for the Connector / Capture layer.

Accepts an operator-supplied query, calls the xAI Grok API, normalizes the
returned answer into a ContentPacket, and returns it ready for capture_content().
No vault I/O here.

DEFAULT INPUT CLASS: 'digest'
    Grok outputs are research synthesis/digest-style artifacts — a single
    curated AI-backed answer. They map to 'digest' (quarantine: Digests/,
    knowledge_class: source-derived).

    'source' applies to discrete single-origin external articles.
    'digest' applies when one artifact synthesizes or reasons over multiple
    sources — which is exactly what a Grok answer is. This is the justified
    default, matching the Perplexity connector pattern.

    The operator may override with --class for edge cases.

DEFAULT SOURCE PLATFORM: 'grok'
    Hard-coded to 'grok'. No override needed unless multi-provider use
    is added in a future pass.

CREDENTIAL HANDLING:
    API key is loaded from the environment variable XAI_API_KEY only.
    Never read from files, never hardcoded, never written to sidecars or logs.

    Required env var: XAI_API_KEY
    Missing key behaviour: raises GrokCredentialError immediately, before
    any network call is made. The error message tells the operator which
    variable to set.

    The API key is NEVER written to:
        - ContentPacket fields
        - sidecar metadata (.meta.json)
        - extra_metadata
        - build logs or archive notes

DEFAULT MODEL: 'grok-3'
    xAI's current flagship model. The operator may override with --model.

    Other usable models at time of writing:
        grok-3-fast    — faster, lower cost
        grok-2         — previous generation

CAPTURE METHOD: 'api'
    capture_method field is 'api' for all API-backed connectors.

ORIGIN KIND DEFAULT: 'ai-generated'
    Grok outputs are AI-generated reasoning/synthesis artifacts. The
    origin_kind field is set to 'ai-generated' by default. The operator
    may override via --origin-kind.

TITLE DERIVATION:
    If --title is not provided, the title is derived from the query:
        - Truncated to 80 characters at a word boundary
        - Used as the sidecar title and filename slug basis
    The operator may override with an explicit --title.

API ENDPOINT:
    Endpoint: POST https://api.x.ai/v1/chat/completions
    Protocol: OpenAI-compatible chat completions format
    Auth: Bearer token from XAI_API_KEY env var
    Stdlib only: urllib.request + json (no external HTTP dependencies)
    Timeout: 60 seconds per request

PROVENANCE FIELDS:
    Top-level ContentPacket fields:
        title           — derived from query or --title override
        input_class     — 'digest' (default) or --class override
        source_platform — 'grok'
        capture_method  — 'api'
        origin_kind     — 'ai-generated' (default) or --origin-kind override
        captured_at     — ISO 8601 UTC timestamp of when the capture ran

    extra_metadata fields (stored in sidecar .meta.json):
        query               — the exact query string sent to the API
        model               — model name as returned by the API
        response_id         — xAI response ID if present
        usage               — token usage dict if returned (prompt/completion/total)
        finish_reason       — finish reason from the first choice if present
        capture_method_detail — 'xai-grok-api-chat-completions'

    NOT stored anywhere:
        - API key
        - Authorization headers
        - Raw HTTP response objects

DEDUP:
    Standard Pass 6 SHA-256 dedup registry applies.
    Dedup key = SHA-256 of the returned answer text (content body).
    Same answer for the same query on repeat calls → duplicate on second
    capture attempt. Operator gets an explicit duplicate result.
    No duplicate file is written.

QUARANTINE DOCTRINE:
    All captures land in 03_INPUTS/00_QUARANTINE/[class]/ (default: Digests/).
    NOT ingested into SIC at capture time.
    Pipeline: query -> API response -> ContentPacket -> capture_content() -> quarantine.
    No auto-promotion. No SIC trigger. Promotion remains explicit and governed.

HONEST LIMITATIONS:
    - One-shot query only — no multi-turn conversation support in this pass
    - No automatic scheduled polling or batch queries
    - No Perplexity-style citation field (xAI chat completions do not include
      a top-level citations list in the standard response; live search is a
      separate API feature not activated in this connector)
    - No live search activation (requires additional API configuration)
    - No watched-folder automation
    - No promotion into knowledge — quarantine only
    - No SIC auto-ingestion
    - No AOR scheduling
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

_ENV_KEY                = "XAI_API_KEY"
_API_URL                = "https://api.x.ai/v1/chat/completions"
_DEFAULT_MODEL          = "grok-3"
_DEFAULT_SOURCE_PLATFORM = "grok"
_DEFAULT_INPUT_CLASS    = INPUT_CLASS_DIGEST
_TITLE_MAX_LEN          = 80
_REQUEST_TIMEOUT        = 60  # seconds


# ── Public exception types ─────────────────────────────────────────────────────

class GrokCredentialError(Exception):
    """
    Raised when XAI_API_KEY is missing or empty.

    This is raised before any network call is attempted.
    The operator should set the XAI_API_KEY environment variable.
    """


class GrokAPIError(Exception):
    """
    Raised when the Grok/xAI API call fails.

    Covers:
        - HTTP errors (4xx, 5xx)
        - Network/connection failures
        - Non-JSON response bodies
        - Empty choices in a successful response
    """


# ── Credential loading ─────────────────────────────────────────────────────────

def _get_api_key() -> str:
    """
    Load the xAI API key from the environment.

    Reads XAI_API_KEY. Raises GrokCredentialError if missing or empty.
    Never reads from files, never hardcodes a default.
    """
    key = os.environ.get(_ENV_KEY, "").strip()
    if not key:
        raise GrokCredentialError(
            f"xAI/Grok API key not found. "
            f"Set the environment variable {_ENV_KEY!r} before capturing. "
            f"Example: set {_ENV_KEY}=xai-... (Windows) or export {_ENV_KEY}=xai-... (Unix)"
        )
    return key


# ── API call ───────────────────────────────────────────────────────────────────

def query_grok(
    query: str,
    *,
    model: str = _DEFAULT_MODEL,
    api_key: str | None = None,
    system: str | None = None,
) -> dict:
    """
    Call the xAI Grok API with a single user query.

    Args:
        query:    The research question or prompt to send.
        model:    Grok model name (default: 'grok-3').
        api_key:  API key string. If None, loads from XAI_API_KEY env var.
        system:   Optional system prompt prepended to the messages list.
                  Keep this minimal — this connector is not a prompt-engineering layer.

    Returns:
        Parsed JSON response dict from the xAI API.

    Raises:
        GrokCredentialError: if api_key is None and env var is missing.
        GrokAPIError: on HTTP errors, network failures, or invalid JSON response.

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
        raise GrokAPIError(
            f"xAI/Grok API returned HTTP {exc.code}: {err_body[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise GrokAPIError(
            f"xAI/Grok API network error: {exc.reason}"
        ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise GrokAPIError(
            f"xAI/Grok API returned non-JSON response: {body[:200]!r}"
        ) from exc


# ── Response normalization ─────────────────────────────────────────────────────

def _extract_content(response: dict) -> str:
    """
    Extract the answer text from a Grok API response.

    Expects OpenAI-compatible chat completions format:
        response['choices'][0]['message']['content']

    Raises GrokAPIError if choices are absent or empty.
    Returns empty string if message content is an empty string.
    """
    choices = response.get("choices", [])
    if not choices:
        raise GrokAPIError(
            "xAI/Grok API returned no choices in response. "
            f"Full response keys: {list(response.keys())}"
        )
    return choices[0].get("message", {}).get("content", "")


def _extract_finish_reason(response: dict) -> str | None:
    """Extract finish_reason from the first choice if present."""
    choices = response.get("choices", [])
    if not choices:
        return None
    return choices[0].get("finish_reason")


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

    cut = query[:_TITLE_MAX_LEN]
    last_space = cut.rfind(" ")
    if last_space > _TITLE_MAX_LEN // 2:
        cut = cut[:last_space]
    return cut.rstrip() + "..."


# ── Public connector API ───────────────────────────────────────────────────────

def capture_from_grok(
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
    Query the xAI Grok API and return a ContentPacket ready for capture_content().

    This is the single public entry point for this connector.
    It handles credential loading, the API call, response normalization,
    and ContentPacket assembly in one step.

    Args:
        query:               Research question or prompt. Required. Must not be empty.
        model:               Grok model to use (default: 'grok-3').
        api_key:             API key string. If None, loads from XAI_API_KEY.
        system:              Optional system prompt (minimal use only).
        title:               Override title. If None, derived from query text.
        input_class:         ContentPacket class (default: 'digest').
        source_platform:     Source platform slug (default: 'grok').
        workspace_hint:      SIC workspace hint for future ingestion. Hint only.
        domain_hint:         ChaseOS domain hint. Hint only.
        project_hint:        Active project hint. Hint only.
        topic_hint:          Subject label hint. Hint only.
        event_date_hint:     ISO 8601 date hint (YYYY-MM-DD). Hint only.
        origin_kind:         Content authorship origin. Defaults to 'ai-generated'.
        desired_output_kind: Intended output type. Hint only.

    Returns:
        ContentPacket with the Grok answer as content and provenance fields
        populated. The packet has NOT been written to quarantine — call
        capture_content(packet, vault_root) to complete the intake.

    Raises:
        ValueError:             if query is empty.
        GrokCredentialError:    if API key is missing (before network call).
        GrokAPIError:           if the API call fails.

    QUARANTINE DOCTRINE:
        This function does not write to vault. SIC is not triggered. No promotion.
        All of that happens in capture_content() and the Gate, after operator review.
    """
    if not query or not query.strip():
        raise ValueError("query must not be empty.")

    response = query_grok(query, model=model, api_key=api_key, system=system)

    content = _extract_content(response)
    if not content.strip():
        content = f"[Grok returned empty content for query: {query!r}]"

    response_id   = response.get("id")
    usage         = response.get("usage", {})
    model_used    = response.get("model", model)
    finish_reason = _extract_finish_reason(response)

    resolved_title = title if title else _make_title(query)
    captured_at    = datetime.now(timezone.utc).isoformat()

    extra_metadata = {
        "query":                 query,
        "model":                 model_used,
        "response_id":           response_id,
        "usage":                 usage,
        "finish_reason":         finish_reason,
        "capture_method_detail": "xai-grok-api-chat-completions",
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
