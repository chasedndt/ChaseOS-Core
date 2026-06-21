"""Runtime schema utilities for ChaseOS."""

from .provenance_validator import is_valid_provenance_block, validate_provenance_block
from .provenance_block import (
    ProvenanceBlock,
    append_lineage_step,
    make_from_sidecar,
    make_from_source_package,
    make_minimal,
    upgrade_verification_status,
)
from .promotion_check import check_promotion_minimum, get_promotion_provenance_tier

__all__ = [
    # validator
    "is_valid_provenance_block",
    "validate_provenance_block",
    # block type + factories
    "ProvenanceBlock",
    "make_minimal",
    "make_from_sidecar",
    "make_from_source_package",
    # mutation helpers
    "append_lineage_step",
    "upgrade_verification_status",
    # promotion check
    "check_promotion_minimum",
    "get_promotion_provenance_tier",
]
