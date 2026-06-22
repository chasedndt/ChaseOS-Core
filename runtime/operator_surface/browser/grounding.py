"""
runtime.operator_surface.browser.grounding

Browser grounding tier selection, target resolution, and fallthrough protocol.

Grounding order (canonical — Tier A → B → C):
  Tier A: STRUCTURED_API — direct DOM / Playwright Locator API
  Tier B: ACCESSIBILITY  — accessibility tree / ARIA roles / computed labels
  Tier C: VISUAL_SCREENSHOT — pixel-level screenshot + vision model

Target selection contract:
  resolve_target() takes a target_hint (selector string, ARIA label, or
  descriptive text) and returns a TargetSelection declaring which tier
  was used, what the resolved reference is, and how confident the resolution is.

Fallthrough protocol:
  1. Try preferred_tier (default: Tier A)
  2. If Tier A fails/empty → fall to Tier B
  3. If Tier B fails/empty → fall to Tier C
  4. If all fail → raise GroundingFailed

GroundingContext:
  Tracks tier usage across an entire run for the audit payload.
  The adapter holds one GroundingContext per run and updates it after
  each execute_step() call.

Architecture: 06_AGENTS/Browser-Operator-Surface.md Section 3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from runtime.operator_surface.capabilities import GroundingMode
from runtime.operator_surface.browser.perception import (
    PageState,
    read_dom_elements,
    read_accessibility_tree,
    capture_screenshot,
)


class GroundingFailed(Exception):
    """
    All grounding tiers exhausted without finding a usable element or state.

    Raised by select_grounding_tier() and resolve_target() when no tier
    returns usable content. The executor catches this as an unrecoverable
    step failure unless the adapter's recover() method handles it.
    """
    pass


@dataclass
class TargetSelection:
    """
    Result of resolving a target hint through the grounding tier protocol.

    tier_used:       which tier successfully resolved the target
    selector_or_ref: the resolved selector / reference string
    method:          resolution method used (e.g. "css_selector", "aria_label",
                     "accessibility_role", "vision_description")
    fallback_count:  how many tiers were tried before finding a match
    confidence_note: optional human-readable confidence indicator
                     (e.g. "exact CSS match", "ARIA role match", "vision estimate")
    """
    tier_used: GroundingMode
    selector_or_ref: str
    method: str
    fallback_count: int = 0
    confidence_note: str = ""


@dataclass
class GroundingContext:
    """
    Run-scoped grounding usage tracker.

    The adapter holds one GroundingContext for the lifetime of a run.
    After each step, call record_tier_use() with the tier that was used.

    to_audit_dict() returns the grounding breakdown for the audit payload.
    """
    _tier_counts: dict[str, int] = field(default_factory=lambda: {
        GroundingMode.STRUCTURED_API.value: 0,
        GroundingMode.ACCESSIBILITY.value: 0,
        GroundingMode.VISUAL_SCREENSHOT.value: 0,
    })
    _fallback_count: int = 0   # total times a fallthrough was triggered

    def record_tier_use(self, tier: GroundingMode, was_fallback: bool = False) -> None:
        """Record that a tier was used for one step."""
        self._tier_counts[tier.value] = self._tier_counts.get(tier.value, 0) + 1
        if was_fallback:
            self._fallback_count += 1

    def to_audit_dict(self) -> dict:
        """Serialize for the audit adapter_payload."""
        return {
            "grounding_tier_counts": dict(self._tier_counts),
            "total_fallbacks": self._fallback_count,
            "primary_tier": self._dominant_tier(),
        }

    def _dominant_tier(self) -> str:
        """Return the tier used most often across the run."""
        if not self._tier_counts:
            return GroundingMode.STRUCTURED_API.value
        return max(self._tier_counts, key=lambda k: self._tier_counts[k])


# ── Target resolution ─────────────────────────────────────────────────────────

def resolve_target(
    page: Any,
    target_hint: str,
    preferred_tier: GroundingMode = GroundingMode.STRUCTURED_API,
) -> TargetSelection:
    """
    Resolve a target hint to a concrete selector or reference through the
    grounding tier fallthrough protocol.

    target_hint: can be any of:
        - CSS selector ("button.submit", "#email-input")
        - ARIA label hint ("Login button", "Search field")
        - Text content hint ("Sign in")
        - Visual description (Tier C fallback)

    Returns TargetSelection describing what was found and how.
    Raises GroundingFailed if all tiers are exhausted.

    PARTIAL — returns Tier A stub selection until Playwright is available.
    When Playwright is available, implement tier-by-tier resolution:
        Tier A: page.locator(target_hint).count() > 0
        Tier B: check accessibility snapshot for matching role/label
        Tier C: describe target to vision model for coordinate-based click
    """
    tier_order = _build_tier_order(preferred_tier)
    fallback_count = 0

    for tier in tier_order:
        try:
            result = _try_tier(page, target_hint, tier)
            if result is not None:
                return TargetSelection(
                    tier_used=tier,
                    selector_or_ref=str(result) if not isinstance(result, str) else result,
                    method=_tier_method(tier),
                    fallback_count=fallback_count,
                    confidence_note=_confidence_note(tier, fallback_count),
                )
        except Exception:
            pass
        fallback_count += 1

    raise GroundingFailed(
        f"All grounding tiers exhausted for target '{target_hint}'. "
        "Page may not contain the target element in any accessible form."
    )


def select_grounding_tier(
    page: Any,
    selector: str,
    preferred_tier: GroundingMode = GroundingMode.STRUCTURED_API,
) -> tuple[GroundingMode, Any]:
    """
    Select the best available grounding tier for the given selector.
    Returns (GroundingMode, result) where result is tier-specific data.

    Tries preferred_tier first; falls through to lower tiers on failure.

    PARTIAL — returns preferred_tier with stub result until Playwright is available.
    """
    tier_order = _build_tier_order(preferred_tier)

    for tier in tier_order:
        try:
            result = _try_tier(page, selector, tier)
            if result is not None:
                return tier, result
        except Exception:
            continue

    raise GroundingFailed(
        f"All grounding tiers exhausted for selector '{selector}'. "
        "Page may not contain the target element in any accessible form."
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _build_tier_order(preferred_tier: GroundingMode) -> list[GroundingMode]:
    """Build tier list starting from preferred_tier."""
    canonical = [
        GroundingMode.STRUCTURED_API,
        GroundingMode.ACCESSIBILITY,
        GroundingMode.VISUAL_SCREENSHOT,
    ]
    if preferred_tier in canonical:
        idx = canonical.index(preferred_tier)
        return canonical[idx:] + canonical[:idx]
    return canonical


def _try_tier(page: Any, selector: str, tier: GroundingMode) -> Optional[Any]:
    """
    Attempt a single grounding tier.
    Returns result data if tier has useful content, None if tier is empty.
    """
    if tier == GroundingMode.STRUCTURED_API:
        elements = read_dom_elements(page, selector)
        return elements if elements else None

    elif tier == GroundingMode.ACCESSIBILITY:
        tree = read_accessibility_tree(page)
        return tree if tree else None

    elif tier == GroundingMode.VISUAL_SCREENSHOT:
        screenshot = capture_screenshot(page)
        return screenshot if screenshot else None

    return None


def _tier_method(tier: GroundingMode) -> str:
    """Resolution method name for a grounding tier."""
    return {
        GroundingMode.STRUCTURED_API: "css_or_locator",
        GroundingMode.ACCESSIBILITY: "accessibility_role_or_label",
        GroundingMode.VISUAL_SCREENSHOT: "vision_description",
    }.get(tier, "unknown")


def _confidence_note(tier: GroundingMode, fallback_count: int) -> str:
    """Human-readable confidence indicator."""
    base = {
        GroundingMode.STRUCTURED_API: "exact DOM match",
        GroundingMode.ACCESSIBILITY: "ARIA role/label match",
        GroundingMode.VISUAL_SCREENSHOT: "vision model estimate",
    }.get(tier, "unknown")
    if fallback_count > 0:
        return f"{base} (after {fallback_count} tier fallback(s))"
    return base


def tier_name(mode: GroundingMode) -> str:
    """Human-readable tier name for logging and audit."""
    return {
        GroundingMode.STRUCTURED_API: "Tier A (DOM/Structured)",
        GroundingMode.ACCESSIBILITY: "Tier B (Accessibility)",
        GroundingMode.VISUAL_SCREENSHOT: "Tier C (Visual/Screenshot)",
    }.get(mode, str(mode))


def grounding_summary(context: GroundingContext) -> str:
    """One-line grounding summary for logs and audit descriptions."""
    d = context.to_audit_dict()
    counts = d["grounding_tier_counts"]
    parts = []
    if counts.get("structured_api", 0):
        parts.append(f"TierA×{counts['structured_api']}")
    if counts.get("accessibility", 0):
        parts.append(f"TierB×{counts['accessibility']}")
    if counts.get("visual_screenshot", 0):
        parts.append(f"TierC×{counts['visual_screenshot']}")
    fb = d["total_fallbacks"]
    suffix = f" ({fb} fallbacks)" if fb else ""
    return ", ".join(parts) + suffix if parts else "no grounding recorded"
