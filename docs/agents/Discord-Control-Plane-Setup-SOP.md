# Discord Control Plane Setup SOP

## Goal

Configure Discord as a bounded ChaseOS operator transport without storing tokens, live IDs, or private channel state in the public repository.

## Prerequisites

- A local ChaseOS instance.
- A Discord bot created in the Discord Developer Portal.
- Environment variables or a local secrets manager for bot credentials.
- A local channel registry file created from the public example.

## Credential Rules

Never write the bot token or webhook URL into Markdown, YAML, JSON examples, logs, screenshots, or exported Core files.

Use environment variables such as:

```text
DISCORD_BOT_CREDENTIAL_REF
DISCORD_GUILD_REF
DISCORD_OPERATOR_CHANNEL_REF
DISCORD_REVIEW_CHANNEL_REF
DISCORD_RUNTIME_STATUS_CHANNEL_REF
```

## Setup Steps

1. Copy the public channel registry example into a private local config path.
2. Replace placeholder channel IDs with real IDs in the private config only.
3. Set credentials through `.env`, keychain, or another local secret store.
4. Run the local Discord binding validator before enabling command dispatch.
5. Start in read-only/status mode.
6. Enable task enqueue or approval routing only after validation passes.

## Validation Checklist

- Token is present only in a private secret store.
- Channel IDs are present only in private config.
- Runtime identities map to public role names, not private account details.
- Unsupported commands fail closed.
- Approval-required commands produce review packets rather than executing directly.
- Logs redact message content where required.

## Failure Handling

If validation fails, stop the adapter and leave Discord in notification-only mode. Do not retry with broader permissions.
