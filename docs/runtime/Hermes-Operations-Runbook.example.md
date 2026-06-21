# Hermes Operations Runbook Example

## Start

Use the local runtime daemon command configured by your ChaseOS instance:

```powershell
python -m runtime.cli.main runtime daemon --runtime hermes --daemon-interval 30 --vault-root "%CHASEOS_VAULT%"
```

## Stop

Use the local process manager or ChaseOS runtime control command for your deployment. Do not publish live process IDs or machine-local paths.

## Health

Recommended checks:

- Runtime status command returns JSON.
- Agent Bus is reachable.
- Credentials are referenced by environment variable only.
- Logs are written to a private runtime log path.

## Failure Handling

- If credential reference is missing, block provider-backed synthesis.
- If Agent Bus is unavailable, block task claim.
- If approval artifact is missing, block write or external actions.
- If startup registration is invalid, leave autostart disabled.

