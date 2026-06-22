"""Acquisition adapters for Pass 1A."""

from .local import AcquiredSource, LocalDeclaredSourceAdapter

__all__ = [
    "AcquiredSource",
    "LocalDeclaredSourceAdapter",
    "VisualCapturePreviewError",
    "preview_visual_capture_acquisition",
]


def __getattr__(name: str) -> object:
    if name in {"VisualCapturePreviewError", "preview_visual_capture_acquisition"}:
        from .visual_capture_adapter import (
            VisualCapturePreviewError,
            preview_visual_capture_acquisition,
        )

        return {
            "VisualCapturePreviewError": VisualCapturePreviewError,
            "preview_visual_capture_acquisition": preview_visual_capture_acquisition,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
