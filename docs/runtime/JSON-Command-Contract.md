# JSON Command Contract

Public ChaseOS CLI surfaces should be scriptable and inspectable.

## Recommended Shape

```json
{
  "ok": true,
  "action": "surface.name",
  "result": {},
  "errors": [],
  "warnings": [],
  "audit_id": null
}
```

## Required Safety Fields for Mutating or Gated Surfaces

- `writes_performed`
- `created_paths`
- `modified_paths`
- `authority_ceiling`
- `manual_step_required`
- `next_allowed_action`
