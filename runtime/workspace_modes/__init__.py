"""Workspace Mode Layer runtime package (ChaseOS Core slice).

Core ships the dependency-free profile model + loader + inference. The heavier
AOR-dispatch / live-execution / approval-gate / product-status modules of the full
Workspace Mode Layer belong to the proprietary runtime and are not part of MIT Core.
"""

from .inference import infer_workspace_mode, is_runtime_mode_path, normalize_workspace_path
from .loader import (
    load_workspace_profile,
    load_workspace_profile_from_mapping,
    load_workspace_profile_or_unknown,
    parse_profile_text,
    resolve_workspace_mode_for_path,
)
from .models import (
    ALLOWED_ADAPTER_CEILING_VALUES,
    ALLOWED_KNOWLEDGE_CLASSES,
    ALLOWED_WORKSPACE_MODES,
    WorkspaceModeProfile,
    WorkspaceModeValidationError,
    build_unknown_profile,
    validate_profile_mapping,
)

__all__ = [
    "ALLOWED_ADAPTER_CEILING_VALUES",
    "ALLOWED_KNOWLEDGE_CLASSES",
    "ALLOWED_WORKSPACE_MODES",
    "WorkspaceModeProfile",
    "WorkspaceModeValidationError",
    "build_unknown_profile",
    "infer_workspace_mode",
    "is_runtime_mode_path",
    "load_workspace_profile",
    "load_workspace_profile_from_mapping",
    "load_workspace_profile_or_unknown",
    "normalize_workspace_path",
    "parse_profile_text",
    "resolve_workspace_mode_for_path",
    "validate_profile_mapping",
]
