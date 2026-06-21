"""Smart-embed-ready ChaseOS audit writeback helpers."""

from .smart_embed import (
    AuditWritebackError,
    build_audit_writeback,
    render_discord_card,
    render_discord_embed,
    render_frontmatter_markdown,
    validate_audit_writeback,
    write_audit_writeback_artifacts,
)

__all__ = [
    "AuditWritebackError",
    "build_audit_writeback",
    "render_discord_card",
    "render_discord_embed",
    "render_frontmatter_markdown",
    "validate_audit_writeback",
    "write_audit_writeback_artifacts",
]
