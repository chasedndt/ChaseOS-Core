"""Structured output validation helpers for sub-agent results."""

from __future__ import annotations


def missing_required_markdown_sections(markdown: str, required_sections: tuple[str, ...]) -> tuple[str, ...]:
    headings = {
        line.lstrip("#").strip().lower()
        for line in markdown.splitlines()
        if line.startswith("#")
    }
    return tuple(section for section in required_sections if section.lower() not in headings)


def validate_structured_markdown_output(
    markdown: str,
    required_sections: tuple[str, ...],
) -> tuple[bool, tuple[str, ...]]:
    missing = missing_required_markdown_sections(markdown, required_sections)
    return (not missing, missing)
