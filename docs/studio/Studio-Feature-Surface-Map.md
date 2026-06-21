# Studio Feature Surface Map

Use this map to decide whether a Studio panel or feature belongs in ChaseOS Core V1.

| Studio Surface | Core Primitive | Public Core Status | Notes |
|---|---|---|---|
| Home / command center | `00_HOME` templates | Include | Use examples, not private current state. |
| Projects | `01_PROJECTS` templates | Include | Public project examples only. |
| Knowledge graph / notes | `02_KNOWLEDGE` templates | Include | Show source/synthesis/promotion model. |
| Inputs / capture | `03_INPUTS` templates | Include | Quarantine and source intake examples only. |
| SOPs | `04_SOPS` docs/templates | Include | Credential and operational boundaries are useful public Core. |
| Templates | `05_TEMPLATES` / `templates/` | Include | Public starter templates. |
| Agents and runtimes | `06_AGENTS` contracts | Include | Governance and adapter docs only, no live credentials/state. |
| Logs and evidence | `07_LOGS` templates | Include | Example indexes; no live logs. |
| Approval center | Gate/governance templates | Include | Operator-confirmed decisions only. |
| Runtime task bus | Agent Bus contracts | Include cautiously | Public schemas/examples; exclude live queues. |
| Packaged Studio app | installer/release channel | Release asset | Do not commit `.exe` to normal source history. |

## V1 Rule

A Studio surface belongs in public Core when it teaches or exercises a reusable ChaseOS contract. It does not belong when it only reflects one operator's private data, local runtime state, or machine-specific implementation history.
