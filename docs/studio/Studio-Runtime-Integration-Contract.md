# Studio Runtime Integration Contract

Studio integrates with ChaseOS runtimes through explicit contracts rather than ambient authority.

## Required Integration Pattern

1. **Discover** a runtime, workflow, or CLI surface from a declared registry/contract.
2. **Show** the operator the requested action, inputs, authority ceiling, and expected write targets.
3. **Run** only through the approved command or workflow surface.
4. **Capture** structured result metadata: status, evidence paths, writes performed, and next manual step.
5. **Render** output in Studio without silently promoting or mutating canonical files.

## Minimum Result Fields

- `ok` or `status`
- `workflow` or `surface`
- `authority_ceiling`
- `writes_performed`
- `created_paths` / `modified_paths`
- `evidence_paths`
- `manual_step_required`
- `next_allowed_action`

## Safety Requirements

- Runtime state lives outside public Core unless represented as sanitized examples.
- Connectors and credentials remain instance-local.
- Studio buttons that perform writes must name the target and approval source.
- Browser, shell, provider, and messaging authority require separate runtime contracts.
