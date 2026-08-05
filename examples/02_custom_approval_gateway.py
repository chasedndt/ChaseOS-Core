"""Implement and register your own Gate provider (ADR-0014).

This is the main extension point in Core. Core defines the *port*; the authority decision
itself is supplied by whatever you register. Out of the box a pure MIT Core install has no
provider bound, so it falls back to **deny-by-default** — an un-kerneled Core never
silently permits a gated operation.

That fail-closed default matters: if you fork Core and never register a provider, gated
operations will be DENIED, not allowed. This script shows both halves — the default denial,
then a custom policy.

Run:  python examples/02_custom_approval_gateway.py
"""

from typing import Any, Optional

from runtime.gate_interface import (
    ActionSpec,
    ApprovalGatewayError,
    check_runtime_operation,
    clear_gate,
    get_approval_gateway,
    register_gate,
)


class ReadOnlyGate:
    """A minimal GateProvider that permits reads and refuses everything else.

    Only ``check_runtime_operation`` carries real policy here; the rest satisfy the
    Protocol. Any object with these methods is a valid provider — Core uses a structural
    Protocol, so you do not need to subclass anything.
    """

    READ_OPERATIONS = {"graph.query", "memory.inspect", "connections.list"}

    def load_adapter_manifest(self, adapter_id: str) -> dict:
        return {"adapter_id": adapter_id, "trust_tier": "read_only"}

    def validate_manifest(self, manifest: dict) -> list[str]:
        return []  # no validation errors

    def check_provenance_minimums(
        self, write_target: str, frontmatter: Optional[dict]
    ) -> tuple[bool, str]:
        if not frontmatter:
            return False, f"{write_target}: provenance frontmatter is required"
        return True, "provenance present"

    def check_runtime_operation(self, operation: str, **kwargs: Any) -> tuple[bool, str]:
        if operation in self.READ_OPERATIONS:
            return True, f"{operation} is a permitted read operation"
        return False, f"{operation} is not permitted by ReadOnlyGate"

    def check_coordination_path(
        self,
        adapter_id: str,
        coordination_sensitive: bool,
        via_bus: bool,
        target_runtime: Optional[str] = None,
    ) -> tuple[bool, str]:
        if coordination_sensitive and not via_bus:
            return False, "coordination-sensitive work must route through the bus"
        return True, "coordination path acceptable"

    def get_runtime_operation_approval_schema(
        self, operation: str, **kwargs: Any
    ) -> Optional[dict]:
        return None


def main() -> None:
    # 1. Default behaviour with no provider registered: deny-by-default.
    clear_gate()
    allowed, reason = check_runtime_operation("graph.query")
    print("no provider registered (pure MIT Core)")
    print(f"  graph.query -> allowed={allowed} ({reason})")
    assert allowed is False, "Core must fail closed when no gate is registered"

    # 2. Register a custom policy and re-check the same operation.
    register_gate(ReadOnlyGate())
    print("\nReadOnlyGate registered")
    for operation in ("graph.query", "vault.write", "connections.list"):
        allowed, reason = check_runtime_operation(operation)
        print(f"  {operation:<20} -> allowed={allowed} ({reason})")

    assert check_runtime_operation("graph.query")[0] is True
    assert check_runtime_operation("vault.write")[0] is False

    # 3. The ApprovalGateway is a separate port for queuing gated actions for a human.
    #    With no approval backend installed, queuing raises rather than proceeding.
    gateway = get_approval_gateway(vault_root=".")
    spec = ActionSpec(
        action_type="write_file",
        target_path="02_KNOWLEDGE/example-note.md",
        content="# Example\n",
        submitted_by="examples/02",
        note="demonstrates the fail-closed approval port",
    )
    print(f"\napproval gateway    : {type(gateway).__name__}")
    print(f"  pending approvals : {gateway.list_pending()}")
    try:
        gateway.queue_for_approval(spec)
        raise AssertionError("expected the deny gateway to refuse queuing")
    except ApprovalGatewayError as exc:
        print(f"  queue_for_approval -> refused: {exc}")

    # Leave global state clean for anything running after this script.
    clear_gate()
    print("\nOK")


if __name__ == "__main__":
    main()
