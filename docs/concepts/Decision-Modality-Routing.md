# Decision Modality Routing

Most systems ask *which model should handle this?* That question is already too late. Core
asks first: **what kind of actor should be responsible for this step at all?**

Four modalities:

| Modality | Use when | Guarantee |
|---|---|---|
| `human` | Judgment, accountability, or consent is required | A person is answerable |
| `rules` | The answer is determinate | Same input, same output, always |
| `ml` | Statistical inference over known distributions | Measurable, drift-detectable |
| `genai` | Open-ended language work | Fluent, and **non-deterministic** |

The mistake this prevents is routing a step to a generative model because it *can* produce
plausible output, when the step actually needed a guarantee that a language model cannot
give.

## Some things cannot be delegated

Certain action classes require a deterministic step no matter how the author wrote the
route — access control, identity, money movement, exact calculation, schema validation,
credential access, security, public publishing, and canonical transitions.

Others additionally require a human checkpoint — destructive actions, legal and ethical
decisions, protected changes, and anything promoting to canonical truth.

Ask for a `genai` step to publish publicly with no deterministic validation and no human,
and the route is **blocked**:

```
public_publish_requires_rules   — action class 'public_publish' must include a
                                  deterministic rules/code step
human_checkpoint_required       — approval-required decisions must contain an
                                  explicit human route step
```

## Each modality carries obligations

A route step is not just a label; the router enforces what each modality owes:

- **`genai`** must explicitly opt into bounded nondeterminism (`nondeterminism_allowed`)
  and declare a non-negative `cost_ceiling_usd`. Unbounded generative steps are rejected —
  you cannot accidentally hand an agent an open budget.
- **`ml`** must declare `model_version`, `evaluation_ref`, and `drift_status`. A step whose
  `drift_status` is `blocked` cannot run.
- **Every material step** must name a `verifier`. A step nobody checks is not a step.

## Inspection never executes

This is the property that makes routing safe to run anywhere:

```python
from runtime.decision_router.router import inspect_decision_contract

result = inspect_decision_contract(contract)
result["authority"]["dispatch_allowed"]              # False
result["authority"]["approval_consumption_allowed"]  # False
result["authority"]["canonical_writeback_allowed"]   # False
```

Inspection validates the contract, selects modalities, and derives the **approval plan** —
accountable human, decision scope, reasons, required evidence, and behaviour on timeout or
denial. It performs none of it. You can inspect a route in CI, in a pull request, or in a
dry run without touching production authority.

Approval defaults are strict: both `on_timeout` and `on_denial` are `block`, and plans are
not `reusable` — one approval does not silently authorise the next run.

## Try it

```bash
python examples/01_inspect_decision_route.py
chaseos decision-route inspect <contract.json> --json
```

The example shows an allowed route and a deliberately unsafe one being blocked.

## Related

- [Authority and Trust](Authority-and-Trust.md) — enforcing the decision
- [Canonical Truth and Promotion](Canonical-Truth-and-Promotion.md) — why canonical writes force approval
- `runtime/decision_router/decision_contract.schema.json` — the contract schema
