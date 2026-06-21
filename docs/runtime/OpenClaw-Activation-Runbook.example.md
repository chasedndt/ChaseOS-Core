# OpenClaw Activation Runbook Example

## Start

Use the local runtime daemon command configured by your ChaseOS instance:

```powershell
python -m runtime.cli.main runtime daemon --runtime openclaw --daemon-interval 30 --vault-root "%CHASEOS_VAULT%"
```

## Discord Preflight

- Discord bot credential reference is available as `DISCORD_BOT_CREDENTIAL_REF`.
- Channel registry uses private local IDs.
- Identity map uses private local IDs.
- Review channel exists for approval-required commands.
- Unsupported commands fail closed.

## Agent Bus Preflight

- Runtime capability manifest exists.
- Runtime profile exists.
- Task packet schema validates.
- Claim/complete behavior is bounded to approved task types.

## Failure Handling

- If Discord validation fails, run notification-only or offline.
- If approval artifacts are missing, block execution.
- If host startup registration is broken, do not broaden permissions.
