"""OpenAI adapter helpers.

This package intentionally contains dry-run builders and policy helpers only.
It does not call the OpenAI API or read credentials.
"""

from runtime.adapters.openai.responses_mcp_payload import (
    build_responses_mcp_payload,
    validate_payload_policy,
    write_payload_draft,
)

__all__ = [
    "build_responses_mcp_payload",
    "validate_payload_policy",
    "write_payload_draft",
]

