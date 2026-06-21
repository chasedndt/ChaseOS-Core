"""
text_extractors.py — SIC Phase 7 Pass 2
Extracts raw text from a file based on detected source type.

Supported:  markdown, plain-text, transcript-verbatim, transcript-summary, research-digest
Deferred:   pdf (no library in venv), audio-derived, webpage/html, clipboard-paste
"""

import re
from pathlib import Path


# ── Extraction method labels (used in Source Package) ────────────────────────

EXTRACTION_METHOD = {
    "markdown":            "direct-text",
    "plain-text":          "direct-text",
    "transcript-verbatim": "transcript-import",
    "transcript-summary":  "transcript-import",
    "research-digest":     "direct-text",
    "clipboard-paste":     "clipboard-paste",
    "document-export":     "direct-text",
    "pdf":                 "pdf-parse",       # deferred — raises gracefully
    "webpage":             "web-extract",     # deferred — raises gracefully
    "audio-derived":       "direct-text",     # deferred — raises gracefully
}


# ── Source type detection ─────────────────────────────────────────────────────

_TRANSCRIPT_PATH_HINTS = (
    "transcript", "transcript-raw", "youtube-notes",
    "lecture", "podcast", "meeting",
)

_DIGEST_PATH_HINTS = (
    "digest", "notebooklm", "perplexity", "grok", "newsletter",
)

_TRANSCRIPT_CONTENT_HINTS = (
    r"^type:\s*raw-input",
    r"input_class:\s*transcript",
    r"source_platform:\s*YouTube",
    r"speaker:",
    r"^\[?\d{1,2}:\d{2}",          # timestamp pattern  00:00 or [00:00]
)

_DIGEST_CONTENT_HINTS = (
    r"^type:\s*raw-input",
    r"input_class:\s*digest",
    r"source_platform:\s*(Perplexity|Grok|Newsletter)",
    r"source_query:",
)


def detect_source_type(input_path: Path, source_type_override: str | None = None) -> str:
    """
    Detect source_type for a given file.

    Priority:
    1. Explicit override
    2. File extension
    3. Path heuristics (folder names)
    4. Content heuristics (first 40 lines)

    Returns a schema-valid source_type string.
    """
    if source_type_override:
        valid = set(EXTRACTION_METHOD.keys())
        if source_type_override not in valid:
            raise ValueError(
                f"source_type_override '{source_type_override}' is not a valid schema type. "
                f"Valid types: {sorted(valid)}"
            )
        return source_type_override

    suffix = input_path.suffix.lower()
    path_lower = str(input_path).lower().replace("\\", "/")

    # Extension-first pass
    if suffix == ".txt":
        return "plain-text"

    if suffix in (".md", ".markdown"):
        # Refine with path + content heuristics
        for hint in _TRANSCRIPT_PATH_HINTS:
            if hint in path_lower:
                return "transcript-verbatim"

        for hint in _DIGEST_PATH_HINTS:
            if hint in path_lower:
                return "research-digest"

        # Content scan — first 40 lines
        try:
            with open(input_path, "r", encoding="utf-8", errors="replace") as f:
                head = "".join(f.readline() for _ in range(40))
        except OSError:
            return "markdown"

        for pattern in _TRANSCRIPT_CONTENT_HINTS:
            if re.search(pattern, head, re.MULTILINE | re.IGNORECASE):
                return "transcript-verbatim"

        for pattern in _DIGEST_CONTENT_HINTS:
            if re.search(pattern, head, re.MULTILINE | re.IGNORECASE):
                return "research-digest"

        return "markdown"

    if suffix == ".pdf":
        return "pdf"

    if suffix in (".html", ".htm"):
        return "webpage"

    # Fallback
    return "plain-text"


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_text(input_path: Path, source_type: str) -> tuple[str, str, str | None]:
    """
    Extract raw text from input_path.

    Returns:
        (raw_text, extraction_method, extraction_notes)

    Raises:
        FileNotFoundError if file missing
        UnsupportedSourceTypeError if type is deferred/unsupported
        ExtractionError if extraction fails
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    method = EXTRACTION_METHOD.get(source_type, "direct-text")
    notes = None

    if source_type == "pdf":
        raise UnsupportedSourceTypeError(
            "pdf",
            "PDF extraction requires pypdf or pdfminer (not installed in venv). "
            "TODO: pip install pypdf and implement pdf_extract() in Phase 7 Pass 3.",
        )

    if source_type == "webpage":
        raise UnsupportedSourceTypeError(
            "webpage",
            "Live webpage extraction is out of scope for Phase 7 Pass 2. "
            "Pre-downloaded HTML files can be read as plain-text if needed.",
        )

    if source_type == "audio-derived":
        raise UnsupportedSourceTypeError(
            "audio-derived",
            "Audio transcription is out of scope for Phase 7 Pass 2.",
        )

    # All text-backed types (markdown, plain-text, transcript-*, research-digest, etc.)
    try:
        raw = input_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExtractionError(f"Could not read file: {exc}") from exc

    if not raw.strip():
        raise ExtractionError(f"File exists but extracted text is empty: {input_path}")

    # Note if YAML frontmatter is present — it will be preserved in normalized text
    if raw.lstrip().startswith("---"):
        notes = "YAML frontmatter detected and preserved in normalized text."

    return raw, method, notes


# ── Custom exceptions ─────────────────────────────────────────────────────────

class UnsupportedSourceTypeError(Exception):
    def __init__(self, source_type: str, detail: str):
        self.source_type = source_type
        self.detail = detail
        super().__init__(f"Unsupported source type '{source_type}': {detail}")


class ExtractionError(Exception):
    pass
