"""
chunking.py — SIC Phase 7 Pass 2
Splits normalized text into overlapping chunks with heading awareness.

Strategy:
- Split at paragraph boundaries (blank lines) where possible
- Build chunks up to chunk_size characters, then emit with overlap
- Track nearest section heading (Markdown ## / ### / # prefix) per chunk
- Each chunk gets a stable chunk_id derived from source_package_id + index
- No tokenizer dependency — char_count is the size metric

Design decision:
  Chunk at paragraph boundaries rather than hard character splits.
  A "paragraph" is a contiguous block separated by one or more blank lines.
  We accumulate paragraphs until we'd exceed chunk_size, then emit and
  carry the overlap (last overlap characters of emitted chunk) into the next.
  This keeps semantic units together and avoids cutting mid-sentence.
"""

import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    chunk_id: str
    chunk_index: int
    text: str
    char_count: int
    char_start: int
    char_end: int
    section_heading: str | None = None


def chunk_text(
    normalized_text: str,
    source_package_id: str,
    chunk_size: int = 1200,
    chunk_overlap: int = 150,
) -> list[Chunk]:
    """
    Chunk normalized_text into overlapping segments.

    Args:
        normalized_text: The normalized source text.
        source_package_id: Used to generate stable chunk_ids.
        chunk_size: Target max chars per chunk.
        chunk_overlap: Chars of overlap carried into next chunk.

    Returns:
        List of Chunk objects in document order.
    """
    if not normalized_text.strip():
        return []

    paragraphs = _split_paragraphs(normalized_text)
    chunks: list[Chunk] = []
    current_text = ""
    current_heading: str | None = None
    char_cursor = 0  # tracks position in normalized_text

    # Track char positions per paragraph for char_start/char_end
    para_positions = _paragraph_positions(normalized_text, paragraphs)

    def _emit_chunk(text: str, heading: str | None, start: int) -> Chunk:
        idx = len(chunks)
        end = start + len(text)
        return Chunk(
            chunk_id=f"{source_package_id}_c{idx:04d}",
            chunk_index=idx,
            text=text,
            char_count=len(text),
            char_start=start,
            char_end=end,
            section_heading=heading,
        )

    pending_paras: list[tuple[str, int]] = []  # (para_text, char_start_in_doc)
    pending_heading: str | None = None
    pending_start: int = 0

    for i, para in enumerate(paragraphs):
        heading = _extract_heading(para)
        if heading:
            current_heading = heading

        para_start = para_positions[i]

        # If adding this para would overflow, emit what we have
        candidate = (current_text + "\n\n" + para).lstrip() if current_text else para

        if len(candidate) > chunk_size and current_text:
            chunks.append(_emit_chunk(current_text, pending_heading, pending_start))

            # Overlap: take the tail of the emitted chunk
            overlap_text = current_text[-chunk_overlap:] if len(current_text) > chunk_overlap else current_text
            overlap_start = pending_start + len(current_text) - len(overlap_text)
            current_text = (overlap_text + "\n\n" + para).lstrip()
            pending_heading = current_heading
            pending_start = overlap_start
        else:
            if current_text:
                current_text = candidate
            else:
                current_text = para
                pending_start = para_start
                pending_heading = current_heading

    # Emit remainder
    if current_text.strip():
        chunks.append(_emit_chunk(current_text.strip(), pending_heading, pending_start))

    return chunks


def chunks_to_dicts(chunks: list[Chunk]) -> list[dict]:
    """Convert Chunk dataclass list to plain dicts for JSON serialization."""
    return [
        {
            "chunk_id": c.chunk_id,
            "chunk_index": c.chunk_index,
            "text": c.text,
            "char_count": c.char_count,
            "char_start": c.char_start,
            "char_end": c.char_end,
            "section_heading": c.section_heading,
            "embedding_vector": None,
            "embedding_model": None,
        }
        for c in chunks
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)


def _extract_heading(para: str) -> str | None:
    """Return the heading text if this paragraph starts with a Markdown heading."""
    m = _HEADING_RE.match(para.lstrip())
    if m:
        return m.group(2).strip()
    return None


def _split_paragraphs(text: str) -> list[str]:
    """Split text on blank-line boundaries, returning non-empty paragraph strings."""
    parts = re.split(r"\n{2,}", text)
    return [p.strip() for p in parts if p.strip()]


def _paragraph_positions(text: str, paragraphs: list[str]) -> list[int]:
    """
    Return the character start position of each paragraph in the original text.
    Uses a simple forward scan — O(n) but fine for typical document sizes.
    """
    positions = []
    search_from = 0
    for para in paragraphs:
        idx = text.find(para, search_from)
        if idx == -1:
            # Fallback: para not found (shouldn't happen) — use last known position
            positions.append(search_from)
        else:
            positions.append(idx)
            search_from = idx + len(para)
    return positions
