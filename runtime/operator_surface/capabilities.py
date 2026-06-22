"""
runtime.operator_surface.capabilities

Enumerations for surface types, operator capabilities, and grounding modes.
These are the vocabulary of the FSOS adapter contract.

All adapters declare their SURFACE_TYPE, CAPABILITIES, and GROUNDING_MODES
as class-level constants conforming to these enums.
"""

from enum import Enum, auto


class SurfaceType(str, Enum):
    """Execution surface types for FSOS adapters."""
    BROWSER = "browser"
    TERMINAL = "terminal"
    DESKTOP = "desktop"
    FILESYSTEM = "filesystem"


class GroundingMode(str, Enum):
    """
    Grounding modes for FSOS adapters that interact with visual/semantic surfaces.
    Declared in priority order — highest (most structured) first.

    Browser canonical order: STRUCTURED_API → ACCESSIBILITY → VISUAL_SCREENSHOT
    Terminal: text output is ground truth; grounding modes not applicable
    Desktop: ACCESSIBILITY → VISUAL_SCREENSHOT
    """
    STRUCTURED_API = "structured_api"       # direct DOM / structured data access
    ACCESSIBILITY = "accessibility"         # accessibility tree / ARIA roles / semantic labels
    VISUAL_SCREENSHOT = "visual_screenshot" # pixel-level screenshot analysis via vision model


class OperatorCapability(str, Enum):
    """
    Operator capabilities that adapters may declare.
    AOR checks declared capabilities against workflow manifest requirements
    before dispatch. An adapter that does not declare a required capability
    will not be dispatched to.
    """

    # ── Browser capabilities ──────────────────────────────────────────
    BROWSER_NAVIGATE = "browser_navigate"       # navigate to URLs
    BROWSER_CLICK = "browser_click"             # click elements
    BROWSER_TYPE = "browser_type"               # type text into inputs
    BROWSER_SCROLL = "browser_scroll"           # scroll pages
    BROWSER_EXTRACT = "browser_extract"         # extract structured data from pages
    BROWSER_SCREENSHOT = "browser_screenshot"   # capture page screenshots
    BROWSER_WAIT = "browser_wait"               # wait for conditions / elements
    BROWSER_TAB_MANAGE = "browser_tab_manage"   # open / close / focus browser tabs
    BROWSER_READ_STATE = "browser_read_state"   # read URL / title / visible text
    BROWSER_KEYBOARD = "browser_keyboard"       # keyboard input — keypress, shortcuts

    # ── Terminal capabilities ─────────────────────────────────────────
    TERMINAL_READ = "terminal_read"             # read shell output / inspect state
    TERMINAL_EXECUTE = "terminal_execute"       # execute shell commands
    TERMINAL_SPAWN = "terminal_spawn"           # spawn subprocesses
    TERMINAL_MONITOR = "terminal_monitor"       # monitor long-running process output

    # ── Desktop capabilities ──────────────────────────────────────────
    DESKTOP_READ = "desktop_read"               # read screen state via accessibility/screenshot
    DESKTOP_CLICK = "desktop_click"             # click UI elements
    DESKTOP_TYPE = "desktop_type"               # type into focused fields
    DESKTOP_WINDOW_MANAGE = "desktop_window_manage"  # focus, move, close windows

    # ── Filesystem capabilities ───────────────────────────────────────
    FILESYSTEM_READ = "filesystem_read"         # read files within allowed paths
    FILESYSTEM_WRITE = "filesystem_write"       # write files within allowed paths
    FILESYSTEM_LIST = "filesystem_list"         # list directory contents
    FILESYSTEM_MOVE = "filesystem_move"         # move files within allowed paths
    FILESYSTEM_DELETE = "filesystem_delete"     # delete files (always requires approval)
