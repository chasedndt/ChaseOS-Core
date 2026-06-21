# runtime/memory/repair/

Structured execution repair memory.

Repair memory records failure/recovery knowledge that may eventually become
runtime-specific guidance. It is not an auto-healing authority layer.

Lifecycle:

1. `incident` - one failure or repair observation.
2. `candidate` - repeated or important pattern, not yet proven.
3. `confirmed` - repair has held across repeated use.
4. `doctrine_candidate` - systemic issue requiring explicit architecture or governance update.

Repair records must keep evidence references and must not silently grant new
permissions.
