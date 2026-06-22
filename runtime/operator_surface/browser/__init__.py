"""
runtime.operator_surface.browser

Browser-specific subpackage for the FSOS Browser Operator Surface.

Modules:
  perception.py — DOM reading, accessibility tree, screenshot capture
  actions.py    — typed action execution (navigate, click, type, scroll, extract)
  grounding.py  — tier selection logic (A → B → C), fallthrough protocol
  replay.py     — reconstruct run from audit events for post-mortem analysis

All modules are PARTIAL — structure is defined; Playwright execution is deferred
until playwright is added to pyproject.toml.

Architecture: 06_AGENTS/Browser-Operator-Surface.md
"""
