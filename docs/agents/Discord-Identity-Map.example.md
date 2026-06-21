# Discord Identity Map Example

Use this as a public-safe template for mapping Discord senders to ChaseOS roles. Do not publish live Discord user IDs.

```yaml
version: 1
identities:
  operator.example:
    discord_user_ref: "env:DISCORD_OPERATOR_USER_ID"
    chaseos_role: "operator"
    authority_ceiling: "approval_review"
  runtime.observer.example:
    discord_user_ref: "env:DISCORD_RUNTIME_OBSERVER_USER_ID"
    chaseos_role: "runtime_observer"
    authority_ceiling: "read_only"
```

## Rules

- Use `env:` references for user IDs.
- Keep live identity maps private.
- Never infer write authority from Discord admin status alone.
- ChaseOS role mapping must be explicit.

