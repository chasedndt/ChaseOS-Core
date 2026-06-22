"""
runtime.operator_surface.adapters

Surface adapter implementations for FSOS.
Each adapter conforms to the contract defined in:
  - 06_AGENTS/Operator-Surface-Adapter-Spec.md
  - runtime/operator_surface/adapters/base.py

Current adapters:
  - browser_adapter.py: Browser (PARTIAL - first implementation target)
  - terminal_adapter.py: Terminal (PARTIAL - bounded read-only subprocess foothold)
  - desktop_adapter.py: Desktop (STUB)
  - filesystem_adapter.py: Filesystem (STUB)
"""
