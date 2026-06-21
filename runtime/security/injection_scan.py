"""Prompt-injection / jailbreak pattern scanner for untrusted ingested content.

ChaseOS treats captured/ingested external content as DATA, never as instructions.
This scanner runs at the capture chokepoint (and can be re-run at promotion) to
*flag* content that contains instruction-like / jailbreak / exfiltration markers,
populating the ``injection_scan`` sidecar field. It does NOT block capture
(quarantine is the containment boundary) — it labels, so downstream consumers and
the operator can see that a quarantined item carries suspicious directives.

Design:
- conservative, high-signal patterns (favor precision over recall to limit false
  positives on legitimate articles about prompt injection);
- also normalizes/flags Unicode obfuscation (zero-width, bidi controls, tag chars)
  that is commonly used to smuggle hidden instructions past human review;
- pure stdlib, no third-party deps, no network, no side effects.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── Unicode obfuscation characters ───────────────────────────────────────────
# Zero-width + BOM, bidi overrides/embeddings, and Unicode "tag" block (used to
# hide instructions invisibly inside otherwise-normal text).
_ZERO_WIDTH = "​‌‍⁠﻿"
_BIDI_CONTROLS = "‪‫‬‭‮⁦⁧⁨⁩"
_OBFUSCATION_CHARS = set(_ZERO_WIDTH + _BIDI_CONTROLS)


def _has_tag_chars(text: str) -> bool:
    return any(0xE0000 <= ord(ch) <= 0xE007F for ch in text)


# ── Injection / jailbreak / exfiltration patterns ────────────────────────────
# (rule_id, compiled regex). Matched case-insensitively against normalized text.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore-previous", re.compile(r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|messages?|context)\b", re.I)),
    ("disregard-above", re.compile(r"\b(?:disregard|forget|override)\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier|system)\b", re.I)),
    ("system-prompt-target", re.compile(r"\b(?:reveal|print|show|repeat|leak|expose|output)\s+(?:your\s+|the\s+)?(?:system\s+prompt|initial\s+instructions?|hidden\s+instructions?|developer\s+(?:message|prompt))\b", re.I)),
    ("role-reassignment", re.compile(r"\byou\s+are\s+now\s+(?:a|an|the|no longer)\b|\bact\s+as\s+(?:if\s+you\s+are\s+)?(?:a|an|the)\b.{0,40}\b(?:no\s+restrictions?|unfiltered|jailbroken)\b", re.I)),
    ("new-instructions", re.compile(r"\b(?:new|updated|real|actual)\s+(?:instructions?|system\s+prompt|task)\s*[:\-]", re.I)),
    ("dan-jailbreak", re.compile(r"\b(?:do\s+anything\s+now|DAN\s+mode|developer\s+mode\s+enabled|jailbreak(?:en)?)\b", re.I)),
    ("chat-role-marker", re.compile(r"<\|(?:im_start|im_end|system|user|assistant)\|>|\[/?INST\]|<<SYS>>|###\s*system\s*:", re.I)),
    ("exfiltrate-credentials", re.compile(r"\b(?:exfiltrate|send|post|upload|transmit|email)\b.{0,40}\b(?:api[\s_-]?key|secret|token|password|credential|\.env|private\s+key|seed\s+phrase)\b", re.I)),
    ("tool-exec-directive", re.compile(r"\b(?:run|execute|eval)\b.{0,30}\b(?:the\s+following|this)\b.{0,30}\b(?:command|code|script|shell)\b", re.I)),
    ("override-safety", re.compile(r"\b(?:ignore|bypass|disable|turn\s+off)\b.{0,30}\b(?:safety|guardrails?|content\s+policy|restrictions?|filters?)\b", re.I)),
    ("prompt-end-injection", re.compile(r"-{3,}\s*end\s+of\s+(?:document|context|input)\s*-{3,}", re.I)),
]


@dataclass
class ScanResult:
    clean: bool
    matches: list[str] = field(default_factory=list)   # rule ids
    obfuscation: list[str] = field(default_factory=list)  # e.g. "zero-width", "bidi-control", "tag-chars"

    def label(self) -> str:
        """Compact sidecar value: 'clean' or 'flagged:rule1,rule2,obf:...'."""
        if self.clean:
            return "clean"
        parts = list(self.matches)
        parts += [f"obf:{o}" for o in self.obfuscation]
        return "flagged:" + ",".join(parts)


def _normalize(text: str) -> str:
    """NFKC fold + strip zero-width so split-word evasions (i g n o r e) and
    homoglyph/compatibility forms still match the patterns."""
    stripped = "".join(ch for ch in text if ch not in _OBFUSCATION_CHARS)
    return unicodedata.normalize("NFKC", stripped)


def scan_text(text: str) -> ScanResult:
    """Scan untrusted text for injection markers + Unicode obfuscation."""
    if not text:
        return ScanResult(clean=True)

    obfuscation: list[str] = []
    if any(ch in _OBFUSCATION_CHARS for ch in text):
        if any(ch in _ZERO_WIDTH for ch in text):
            obfuscation.append("zero-width")
        if any(ch in _BIDI_CONTROLS for ch in text):
            obfuscation.append("bidi-control")
    if _has_tag_chars(text):
        obfuscation.append("tag-chars")

    normalized = _normalize(text)
    matches = [rule_id for rule_id, pattern in _PATTERNS if pattern.search(normalized)]

    clean = not matches and not obfuscation
    return ScanResult(clean=clean, matches=matches, obfuscation=obfuscation)


def scan_label(text: str) -> str:
    """Convenience for the sidecar ``injection_scan`` field."""
    return scan_text(text).label()
