"""
normalization.py — SIC Phase 7 Pass 2
Normalizes raw extracted text into a clean, consistent representation.

Rules:
- Normalize line endings (CRLF → LF)
- Remove null bytes and non-printable garbage (preserve Unicode text)
- Collapse runs of 3+ blank lines to 2
- Strip trailing whitespace per line
- Preserve headings, paragraph breaks, and basic structure
- This is normalization, not summarization — no content is rewritten
"""

import hashlib
import re


TEXT_REPAIR_POLICY_ID = "source-intelligence-common-cp1252-mojibake-repair.v1"

_MOJIBAKE_RUN = re.compile(
    "[\\u00c2\\u00c3\\u00e2]"
    "[\\u0080-\\u00ff\\u0152\\u0153\\u0160\\u0161\\u0178\\u017d\\u017e"
    "\\u0192\\u02c6\\u02dc\\u2018-\\u201d\\u2020-\\u2026\\u2030"
    "\\u2039\\u203a\\u20ac\\u2122]*"
)
_MOJIBAKE_MARKERS = ("\u00c2", "\u00c3", "\u00e2", "\ufffd")


def normalize_text(raw_text: str) -> str:
    """
    Normalize raw extracted text.

    Returns cleaned text. Does not alter word content or reorder sections.
    """
    # 1. Normalize line endings
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. Remove null bytes and C0 control characters except \n and \t
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # 3. Strip trailing whitespace per line (preserve indentation)
    lines = [line.rstrip() for line in text.split("\n")]

    # 4. Collapse runs of more than 2 consecutive blank lines to exactly 2
    result_lines = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 2:
                result_lines.append(line)
        else:
            blank_run = 0
            result_lines.append(line)

    # 5. Strip leading/trailing blank lines from the whole document
    text = "\n".join(result_lines).strip()

    return text


def normalize_text_with_metadata(raw_text: str) -> tuple[str, dict]:
    """Normalize text and return digest-backed source text quality metadata."""

    repaired_text, repair = repair_common_mojibake(raw_text)
    normalized = normalize_text(repaired_text)
    metadata = {
        "policy_id": TEXT_REPAIR_POLICY_ID,
        "original_text_sha256": _sha256_text(raw_text),
        "repaired_text_sha256": _sha256_text(repaired_text),
        "normalized_text_sha256": _sha256_text(normalized),
        "original_text_char_count": len(raw_text),
        "repaired_text_char_count": len(repaired_text),
        "normalized_text_char_count": len(normalized),
        "encoding_repair_applied": repair["encoding_repair_applied"],
        "encoding_repair_replacement_count": repair["encoding_repair_replacement_count"],
        "encoding_repair_replacements_preview": repair["encoding_repair_replacements_preview"],
        "warnings": repair["warnings"],
    }
    return normalized, metadata


def repair_common_mojibake(raw_text: str) -> tuple[str, dict]:
    """Repair common UTF-8 text that was decoded as Windows-1252."""

    replacements: list[dict] = []

    def replace_match(match: re.Match[str]) -> str:
        segment = match.group(0)
        repaired = _repair_cp1252_utf8_segment(segment)
        if repaired == segment:
            return segment
        replacements.append(
            {
                "offset": match.start(),
                "original_unicode_escape": _unicode_escape(segment),
                "repaired_unicode_escape": _unicode_escape(repaired),
            }
        )
        return repaired

    repaired_text = _MOJIBAKE_RUN.sub(replace_match, raw_text)
    metadata = {
        "policy_id": TEXT_REPAIR_POLICY_ID,
        "encoding_repair_applied": bool(replacements),
        "encoding_repair_replacement_count": len(replacements),
        "encoding_repair_replacements_preview": replacements[:10],
        "warnings": ["common_cp1252_mojibake_repaired"] if replacements else [],
    }
    return repaired_text, metadata


def _repair_cp1252_utf8_segment(segment: str) -> str:
    try:
        candidate = segment.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return segment
    if candidate == segment:
        return segment
    if _mojibake_score(candidate) >= _mojibake_score(segment):
        return segment
    return candidate


def _mojibake_score(value: str) -> int:
    score = 0
    for marker in _MOJIBAKE_MARKERS:
        score += value.count(marker)
    score += sum(1 for char in value if "\u0080" <= char <= "\u009f")
    return score


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unicode_escape(value: str) -> str:
    return value.encode("unicode_escape").decode("ascii")
