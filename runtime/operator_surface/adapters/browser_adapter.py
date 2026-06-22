"""
runtime.operator_surface.adapters.browser_adapter

Browser Operator Surface Adapter — first FSOS child execution surface.

Pass 3 additions:
  - Real Playwright initialize() / teardown() — isolated headless Chromium context
  - _PLAYWRIGHT_AVAILABLE module-level flag — graceful stub fallback if not installed
  - _adapter_mode field — "playwright" or "stub"
  - All 18 handlers delegate to browser/actions.py functions (page=None → stub)
  - Real recover() — screenshot + Escape + go_back when browser active
  - _action_result_to_step_result() helper — ActionResult → StepResult
  - _current_url tracking on navigate/tab_open/tab_focus
  - build_audit_payload() updated — adapter_mode, playwright_version

Status: PARTIAL (Pass 3)
- All action handlers: REAL (delegate to actions.py / page=None stub pattern)
- initialize() / teardown(): REAL Playwright lifecycle
- recover(): REAL when playwright mode; stub fallthrough otherwise
- Playwright runtime required: pip install playwright && playwright install chromium
- Tier B / Tier C (accessibility, screenshot-vision) execution: future pass

Grounding hierarchy (Tier A → B → C):
  Tier A: DOM / structured browser access via Playwright Locator API  ← ACTIVE
  Tier B: Accessibility tree via page.accessibility.snapshot()          ← declared
  Tier C: Screenshot + vision model analysis                            ← declared

Architecture: 06_AGENTS/Browser-Operator-Surface.md
Spec: 06_AGENTS/Operator-Surface-Adapter-Spec.md
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from runtime.operator_surface.adapters.base import OperatorSurfaceAdapterBase
from runtime.operator_surface.capabilities import (
    OperatorCapability,
    SurfaceType,
    GroundingMode,
)
from runtime.operator_surface.contracts import (
    OperatorScope,
    OperatorSession,
    StepResult,
    RecoveryResult,
)
from runtime.operator_surface.events import OperatorEvent, OperatorEventType
from runtime.operator_surface.recovery import (
    UnrecoverableFailure,
    build_recovery_started_event,
    build_recovery_complete_event,
)
from runtime.operator_surface.browser.grounding import GroundingContext
from runtime.operator_surface.browser.actions import (
    ActionResult,
    navigate,
    back,
    forward,
    reload,
    tab_open,
    tab_close,
    tab_focus,
    click,
    type_text,
    keypress,
    scroll,
    wait_for,
    read_url,
    read_title,
    read_visible_text,
    extract,
    screenshot,
    snapshot_interactive,
    click_index,
)

# ── Playwright availability guard ─────────────────────────────────────────────

try:
    from playwright.sync_api import sync_playwright as _sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _sync_playwright = None  # type: ignore[assignment]
    _PLAYWRIGHT_AVAILABLE = False


def _discover_playwright_chromium_executable() -> Optional[Path]:
    """
    Return a usable local Chromium executable for Playwright launch, if one is discoverable.

    This repairs a common WSL failure mode where Playwright's Python package is present
    but its default browser revision path is stale while another downloaded revision exists
    under the shared ms-playwright cache. The helper is conservative: it only returns an
    executable file and otherwise lets Playwright use its normal packaged resolution.
    """
    env_path = os.environ.get("CHASEOS_CHROMIUM_EXECUTABLE_PATH")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    roots: list[Path] = []
    for raw_root in (
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        "~/.cache/ms-playwright",  # expanduser() below resolves to the current user's home
    ):
        if raw_root:
            root = Path(raw_root).expanduser()
            if root.exists() and root not in roots:
                roots.append(root)

    candidates: list[Path] = []
    for root in roots:
        candidates.extend(root.glob("chromium-*/chrome-linux64/chrome"))
        candidates.extend(root.glob("chromium-*/chrome-linux/chrome"))

    executable_candidates = [p for p in candidates if p.is_file() and os.access(p, os.X_OK)]
    if not executable_candidates:
        return None
    return sorted(executable_candidates, key=lambda p: (p.parent.parent.name, str(p)), reverse=True)[0]


class BrowserAdapter(OperatorSurfaceAdapterBase):
    """
    FSOS Browser Operator Surface Adapter.

    Pass 3: Real Playwright lifecycle + all 18 handlers delegating to
    browser/actions.py. page=None path preserves stub behavior when
    Playwright is unavailable or not yet initialized.

    Implementation status: PARTIAL (Pass 3)
    - Class declarations and contract conformance: COMPLETE
    - All action type routing and StepResult contracts: COMPLETE
    - initialize() / teardown(): REAL (Playwright lifecycle)
    - All 18 action handlers: REAL (actions.py delegation)
    - Tier B (accessibility) / Tier C (vision) execution: future pass

    Requires: playwright Python package + `playwright install chromium`
    Falls back to stub mode automatically if Playwright is unavailable.

    See: 06_AGENTS/Browser-Operator-Surface.md Section 8
    """

    # ── Identity ──────────────────────────────────────────────────────────
    ADAPTER_ID = "browser-playwright-v1"
    SURFACE_TYPE = SurfaceType.BROWSER
    ADAPTER_VERSION = "0.3.0"
    ADAPTER_STATUS = "partial-pass3"
    DESCRIPTION = "Browser Operator Surface via Playwright — first FSOS child slice"

    # ── Capabilities ──────────────────────────────────────────────────────
    CAPABILITIES = frozenset({
        OperatorCapability.BROWSER_NAVIGATE,
        OperatorCapability.BROWSER_CLICK,
        OperatorCapability.BROWSER_TYPE,
        OperatorCapability.BROWSER_SCROLL,
        OperatorCapability.BROWSER_EXTRACT,
        OperatorCapability.BROWSER_SCREENSHOT,
        OperatorCapability.BROWSER_WAIT,
        OperatorCapability.BROWSER_TAB_MANAGE,
        OperatorCapability.BROWSER_READ_STATE,
        OperatorCapability.BROWSER_KEYBOARD,
    })

    # ── Scope requirements ────────────────────────────────────────────────
    REQUIRED_SCOPE_FIELDS = frozenset({"target_uris", "allowed_origins"})
    FORBIDDEN_SCOPE_PROPERTIES = frozenset({"credential_access"})

    # ── Permissions ───────────────────────────────────────────────────────
    MIN_TRUST_TIER = 2

    # ── Approval-required action classes ──────────────────────────────────
    APPROVAL_REQUIRED_ACTIONS = frozenset({
        "form_submit",
        "credential_field_fill",
        "file_download",
        "navigate_external_domain",
        "cookie_consent_accept",
    })

    # ── Grounding modes — Tier A → B → C ─────────────────────────────────
    GROUNDING_MODES = [
        GroundingMode.STRUCTURED_API,
        GroundingMode.ACCESSIBILITY,
        GroundingMode.VISUAL_SCREENSHOT,
    ]

    # ── Supported action types (for validation and routing) ───────────────
    SUPPORTED_ACTION_TYPES = frozenset({
        # Navigation
        "navigate", "back", "forward", "reload",
        # Tab management
        "tab_open", "tab_close", "tab_focus",
        # Interaction
        "click", "type", "keypress", "scroll", "wait_for", "click_index",
        # State reads
        "read_url", "read_title", "read_visible_text", "snapshot_interactive",
        # Extraction
        "extract", "screenshot",
    })

    def __init__(self):
        self._scope: Optional[OperatorScope] = None
        self._session: Optional[OperatorSession] = None
        self._playwright = None         # playwright.sync_api._generated.Playwright
        self._browser = None            # playwright.sync_api._generated.Browser
        self._browser_context = None    # playwright.sync_api._generated.BrowserContext
        self._page = None               # playwright.sync_api._generated.Page
        self._adapter_mode: str = "stub"  # "playwright" or "stub"
        self._playwright_launch_error: Optional[str] = None
        self._chromium_executable_path: Optional[str] = None
        self._current_url: Optional[str] = None
        self._steps_executed = 0
        self._tabs_opened = 0
        self._grounding_ctx = GroundingContext()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def initialize(self, scope: OperatorScope, session: OperatorSession) -> None:
        """
        Initialize an isolated headless Chromium context for this run.

        The context is ephemeral — no cookies, credentials, or state from the
        operator's personal browser profile are carried in (storage_state=None).

        If Playwright is unavailable or launch fails, silently falls back to
        stub mode — all action handlers remain functional via page=None path.

        Never fails silently on scope/session assignment.
        """
        self._scope = scope
        self._session = session
        self._grounding_ctx = GroundingContext()
        self._tabs_opened = 0
        self._steps_executed = 0
        self._current_url = None
        self._adapter_mode = "stub"
        self._playwright_launch_error = None
        self._chromium_executable_path = None

        if _PLAYWRIGHT_AVAILABLE:
            try:
                self._playwright = _sync_playwright().start()
                executable_path = _discover_playwright_chromium_executable()
                launch_kwargs: dict[str, object] = {"headless": True}
                if executable_path is not None:
                    launch_kwargs["executable_path"] = str(executable_path)
                    self._chromium_executable_path = str(executable_path)
                self._browser = self._playwright.chromium.launch(**launch_kwargs)
                self._browser_context = self._browser.new_context(
                    storage_state=None,      # isolated — no credential bleed
                    extra_http_headers={},
                )
                self._page = self._browser_context.new_page()
                self._adapter_mode = "playwright"
            except Exception as exc:
                # Playwright available but launch failed — fall back to stub while
                # preserving the exact reason in audit payload for repairability.
                self._playwright_launch_error = str(exc)
                try:
                    if self._playwright is not None:
                        self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
                self._browser = None
                self._browser_context = None
                self._page = None
                self._adapter_mode = "stub"

    def plan(self, goal: str, context: dict) -> list[dict]:
        """
        Produce an ordered list of browser action steps for the goal.

        PARTIAL — callers pass pre-declared manifest steps via the executor.
        Reserved for future AOR-dispatched dynamic planning.
        """
        return []

    def execute_step(
        self, step: dict, emit_event: Callable[[OperatorEvent], None]
    ) -> StepResult:
        """
        Execute a single browser action step.

        Routes to typed handler by action_type. Unknown types raise ValueError.
        Each handler delegates to browser/actions.py with self._page.
        """
        action_type = step.get("action_type", "unknown")
        step_index = step.get("step_index", self._steps_executed)

        handler = {
            # Navigation
            "navigate":          self._execute_navigate,
            "back":              self._execute_back,
            "forward":           self._execute_forward,
            "reload":            self._execute_reload,
            # Tab management
            "tab_open":          self._execute_tab_open,
            "tab_close":         self._execute_tab_close,
            "tab_focus":         self._execute_tab_focus,
            # Interaction
            "click":             self._execute_click,
            "type":              self._execute_type,
            "keypress":          self._execute_keypress,
            "scroll":            self._execute_scroll,
            "wait_for":          self._execute_wait_for,
            "click_index":       self._execute_click_index,
            # State reads
            "read_url":          self._execute_read_url,
            "read_title":        self._execute_read_title,
            "read_visible_text": self._execute_read_visible_text,
            "snapshot_interactive": self._execute_snapshot_interactive,
            # Extraction
            "extract":           self._execute_extract,
            "screenshot":        self._execute_screenshot,
        }.get(action_type)

        if handler is None:
            supported = ", ".join(sorted(self.SUPPORTED_ACTION_TYPES))
            raise ValueError(
                f"Unknown browser action type: '{action_type}'. "
                f"Supported: {supported}"
            )

        result = handler(step, step_index)
        self._steps_executed += 1

        # Update grounding context from result
        if result.grounding_mode_used:
            try:
                tier = GroundingMode(result.grounding_mode_used)
                self._grounding_ctx.record_tier_use(tier)
            except ValueError:
                pass

        return result

    def recover(
        self, failed_step: dict, emit_event: Callable[[OperatorEvent], None]
    ) -> RecoveryResult:
        """
        Attempt browser-specific recovery after a step failure.

        When playwright mode is active:
          1. Capture recovery screenshot
          2. Press Escape to dismiss modals
          3. Navigate back to last known-good URL

        Falls back to stub recovery when browser is not active.
        """
        run_id = self._session.run_id if self._session else ""
        step_index = failed_step.get("step_index", 0)

        emit_event(build_recovery_started_event(
            run_id=run_id,
            surface=SurfaceType.BROWSER.value,
            step_index=step_index,
            failed_step=failed_step,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

        recovery_actions = []

        if self._page is not None:
            # Step 1 — capture recovery screenshot
            try:
                screenshot_path = f"recovery_{run_id[:8]}_{step_index}.png"
                self._page.screenshot(path=screenshot_path)
                recovery_actions.append(f"screenshot_captured: {screenshot_path}")
            except Exception:
                pass

            # Step 2 — dismiss modal overlay
            try:
                self._page.keyboard.press("Escape")
                recovery_actions.append("escape_pressed")
            except Exception:
                pass

            # Step 3 — navigate back to last known URL
            if self._current_url:
                try:
                    self._page.go_back(wait_until="domcontentloaded")
                    recovery_actions.append(f"back_to: {self._current_url}")
                except Exception:
                    pass
        else:
            recovery_actions.append("recovery_stub_no_browser")

        emit_event(build_recovery_complete_event(
            run_id=run_id,
            surface=SurfaceType.BROWSER.value,
            step_index=step_index,
            recovery_actions=recovery_actions,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

        return RecoveryResult(
            attempted=True,
            success=True,
            recovery_actions=recovery_actions,
            final_surface_state=self._current_url or "unknown",
        )

    def teardown(
        self, outcome: str, emit_event: Callable[[OperatorEvent], None]
    ) -> None:
        """
        Close browser context, browser, and playwright instance after run.

        Always called regardless of outcome. Swallows all teardown exceptions —
        the audit is already written by this point.

        Teardown order: context → browser → playwright.stop()
        """
        try:
            if self._browser_context is not None:
                self._browser_context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass

        self._browser_context = None
        self._page = None
        self._browser = None
        self._playwright = None
        # _adapter_mode is NOT reset here — it records how the run executed
        # and is read by build_audit_payload() after teardown completes.
        self._current_url = None

    def build_audit_payload(self) -> dict:
        """
        Return browser-specific audit fields for the OperatorRunAudit adapter_payload.
        """
        playwright_version = None
        if _PLAYWRIGHT_AVAILABLE:
            try:
                import playwright
                playwright_version = getattr(playwright, "__version__", "installed")
            except Exception:
                playwright_version = "installed"

        return {
            "adapter_id": self.ADAPTER_ID,
            "adapter_version": self.ADAPTER_VERSION,
            "surface_type": self.SURFACE_TYPE.value,
            "adapter_status": self.ADAPTER_STATUS,
            "adapter_mode": self._adapter_mode,
            "playwright_available": _PLAYWRIGHT_AVAILABLE,
            "playwright_version": playwright_version,
            "playwright_launch_error": self._playwright_launch_error,
            "chromium_executable_path": self._chromium_executable_path,
            "steps_executed": self._steps_executed,
            "tabs_opened": self._tabs_opened,
            "final_url": self._current_url,
            "grounding_breakdown": self._grounding_ctx.to_audit_dict(),
            "supported_action_types": sorted(self.SUPPORTED_ACTION_TYPES),
            "implementation_note": (
                "PARTIAL (Pass 3) — all 18 action handlers delegate to actions.py; "
                "Playwright initialize/teardown real; Tier B/C execution future pass."
            ),
        }

    # ── ActionResult → StepResult bridge ────────────────────────────────

    @staticmethod
    def _to_step(result: ActionResult, step_index: int) -> StepResult:
        """Convert ActionResult from actions.py to StepResult for executor."""
        return StepResult(
            step_index=step_index,
            success=result.success,
            action_type=result.action_type,
            target=result.target,
            output=result.output,
            grounding_mode_used=result.grounding_mode_used,
            error=result.error,
        )

    # ── Navigation handlers ───────────────────────────────────────────────

    def _execute_navigate(self, step: dict, step_index: int) -> StepResult:
        """Navigate to target URL. Enforces scope; ScopeViolation propagates."""
        url = step.get("target", "")
        wait_until = step.get("wait_until", "domcontentloaded")
        result = navigate(self._page, url, self._scope, wait_until)
        if result.success:
            self._current_url = result.output.get("url", url)
        return self._to_step(result, step_index)

    def _execute_back(self, step: dict, step_index: int) -> StepResult:
        """Navigate back in browser history."""
        result = back(self._page)
        if result.success and self._page is not None:
            self._current_url = result.output.get("final_url", self._current_url)
        return self._to_step(result, step_index)

    def _execute_forward(self, step: dict, step_index: int) -> StepResult:
        """Navigate forward in browser history."""
        result = forward(self._page)
        if result.success and self._page is not None:
            self._current_url = result.output.get("final_url", self._current_url)
        return self._to_step(result, step_index)

    def _execute_reload(self, step: dict, step_index: int) -> StepResult:
        """Reload current page."""
        wait_until = step.get("wait_until", "domcontentloaded")
        result = reload(self._page, wait_until)
        return self._to_step(result, step_index)

    # ── Tab management handlers ───────────────────────────────────────────

    def _execute_tab_open(self, step: dict, step_index: int) -> StepResult:
        """Open new tab and navigate to target URL. Scope enforced on URL."""
        url = step.get("target", "")
        result = tab_open(self._browser_context, url, self._scope)
        if result.success:
            self._tabs_opened += 1
            # After tab_open, active page is the new tab
            if self._browser_context is not None:
                pages = self._browser_context.pages
                if pages:
                    self._page = pages[-1]
                    self._current_url = result.output.get("url", url)
        return self._to_step(result, step_index)

    def _execute_tab_close(self, step: dict, step_index: int) -> StepResult:
        """Close tab matching target URL prefix."""
        target_url = step.get("target", "")
        result = tab_close(self._browser_context, target_url)
        if result.success and self._browser_context is not None:
            pages = self._browser_context.pages
            if pages:
                self._page = pages[-1]
                self._current_url = self._page.url
            else:
                self._page = None
                self._current_url = None
        return self._to_step(result, step_index)

    def _execute_tab_focus(self, step: dict, step_index: int) -> StepResult:
        """Bring focus to tab matching target URL prefix."""
        target_url = step.get("target", "")
        result = tab_focus(self._browser_context, target_url)
        if result.success and self._browser_context is not None:
            # Update self._page to the focused tab
            pages = self._browser_context.pages
            matching = [p for p in pages if p.url.startswith(target_url)]
            if matching:
                self._page = matching[0]
                self._current_url = self._page.url
        return self._to_step(result, step_index)

    # ── Interaction handlers ──────────────────────────────────────────────

    def _execute_click(self, step: dict, step_index: int) -> StepResult:
        """Click element by CSS selector."""
        selector = step.get("target", "")
        result = click(self._page, selector, self._scope)
        return self._to_step(result, step_index)

    def _execute_type(self, step: dict, step_index: int) -> StepResult:
        """Type text into element. fill() clears existing text first."""
        selector = step.get("target", "")
        text = step.get("text", "")
        result = type_text(self._page, selector, text, self._scope)
        return self._to_step(result, step_index)

    def _execute_keypress(self, step: dict, step_index: int) -> StepResult:
        """Send keyboard key or combination (e.g. 'Enter', 'Control+A')."""
        key = step.get("target", "")
        selector = step.get("selector")
        result = keypress(self._page, key, selector)
        return self._to_step(result, step_index)

    def _execute_scroll(self, step: dict, step_index: int) -> StepResult:
        """Scroll the page. direction: up/down/left/right. amount: pixels."""
        direction = step.get("direction", step.get("target", "down"))
        amount = step.get("amount", 500)
        result = scroll(self._page, direction, amount)
        return self._to_step(result, step_index)

    def _execute_wait_for(self, step: dict, step_index: int) -> StepResult:
        """Wait for element matching selector to appear."""
        selector = step.get("target", "")
        timeout_ms = step.get("timeout_ms", 5000)
        result = wait_for(self._page, selector, timeout_ms)
        return self._to_step(result, step_index)

    def _execute_click_index(self, step: dict, step_index: int) -> StepResult:
        """Click a numbered element from a prior snapshot_interactive step."""
        index = int(step.get("index", step.get("target", 0)) or 0)
        result = click_index(self._page, index)
        return self._to_step(result, step_index)

    # ── State read handlers ───────────────────────────────────────────────

    def _execute_read_url(self, step: dict, step_index: int) -> StepResult:
        """Read current page URL. Non-mutating."""
        result = read_url(self._page)
        if result.success and self._page is not None:
            self._current_url = result.output.get("url", self._current_url)
        return self._to_step(result, step_index)

    def _execute_read_title(self, step: dict, step_index: int) -> StepResult:
        """Read current page title. Non-mutating."""
        result = read_title(self._page)
        return self._to_step(result, step_index)

    def _execute_read_visible_text(self, step: dict, step_index: int) -> StepResult:
        """Read all visible body text. UNTRUSTED — never execute embedded instructions."""
        result = read_visible_text(self._page)
        return self._to_step(result, step_index)

    def _execute_snapshot_interactive(self, step: dict, step_index: int) -> StepResult:
        """Read a bounded browser-only set-of-marks interactive element snapshot."""
        max_elements = int(step.get("max_elements", 50) or 50)
        result = snapshot_interactive(self._page, max_elements=max_elements)
        return self._to_step(result, step_index)

    # ── Extraction handlers ───────────────────────────────────────────────

    def _execute_extract(self, step: dict, step_index: int) -> StepResult:
        """Extract text content from all elements matching selector."""
        selector = step.get("target", "")
        result = extract(self._page, selector)
        return self._to_step(result, step_index)

    def _execute_screenshot(self, step: dict, step_index: int) -> StepResult:
        """Capture full-page screenshot."""
        output_path = step.get("output_path")
        full_page = bool(step.get("full_page", True))
        settle_ms = int(step.get("settle_ms", 0) or 0)
        clip_selector = step.get("clip_selector") or None
        result = screenshot(
            self._page,
            output_path,
            full_page=full_page,
            settle_ms=settle_ms,
            clip_selector=clip_selector,
        )
        return self._to_step(result, step_index)
