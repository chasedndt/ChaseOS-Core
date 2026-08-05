# ChaseOS Core examples

Runnable scripts showing how to use Core **as a library inside your own project** — no
fork, no vault directory, no repo checkout required.

```bash
pip install chaseos-core
python examples/01_inspect_decision_route.py
python examples/02_custom_approval_gateway.py
python examples/03_load_connection_manifests.py
python examples/04_screen_untrusted_input.py
python examples/05_end_to_end_pipeline.py
```

Each script is self-contained, prints what it is doing, and exits non-zero if an assertion
fails — so they double as a smoke test of a real install.

| Example | Shows | Needs a vault root? |
|---|---|---|
| [`01_inspect_decision_route.py`](01_inspect_decision_route.py) | Validate a decision contract and derive an approval plan without executing anything | No |
| [`02_custom_approval_gateway.py`](02_custom_approval_gateway.py) | Implement the ADR-0014 `GateProvider` port and register it — the main extension point | No |
| [`03_load_connection_manifests.py`](03_load_connection_manifests.py) | Load bundled provider manifests and your own custom manifest directory | No |
| [`04_screen_untrusted_input.py`](04_screen_untrusted_input.py) | Screen inbound text for injection and Unicode obfuscation; audit a tree for credential shapes | No |
| [`05_end_to_end_pipeline.py`](05_end_to_end_pipeline.py) | All of the above in sequence: screen → route → authority → provenance → approval | No |

New to the project? Read [docs/concepts/](../docs/concepts/) first — it explains the ideas
these scripts demonstrate, in order.

If you only run one, run **05**: it shows the whole authority pipeline refusing an unsafe
action, with each refusal attributable to a specific check.

## Which parts of Core are library-usable?

Not all of Core is. These entry points are pure library calls with no directory layout
requirement:

| Module | Entry points | Vault root |
|---|---|---|
| `runtime.decision_router.router` | `load_decision_contract`, `inspect_decision_contract` | Not required |
| `runtime.gate_interface` | `register_gate`, `get_gate`, `check_runtime_operation`, `ApprovalGateway`, `ActionSpec` | Not required |
| `runtime.connections.manifests` | `load_manifest`, `list_provider_manifests`, `parse_manifest` | Not required |
| `runtime.repo_secret_audit` | `audit_repo_secrets`, `format_repo_secret_audit` | Not required (takes a path) |
| `runtime.security.injection_scan` | `scan_text`, `scan_label` | Not required |
| `runtime.connections.store` | SQLite registry init/seed | **Required** (writes `.chaseos/`) |
| `runtime.aor` (workflow execution) | `chaseos run` | **Required**, and currently fork-only — see [ROADMAP.md](../ROADMAP.md) |

If you only want the governance primitives — modality routing and the approval port — you
need none of the ChaseOS folder structure.
