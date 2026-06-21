# Discord Command Envelope Schema

This document describes the public-safe shape of a Discord command envelope before it becomes a ChaseOS task packet or approval request.

```json
{
  "envelope_version": "1.0",
  "source": "discord",
  "guild_ref": "env:DISCORD_GUILD_ID",
  "channel_ref": "env:DISCORD_OPERATOR_CHANNEL_ID",
  "message_ref": "discord-message-id-placeholder",
  "sender": {
    "identity_ref": "operator.example",
    "trust_tier": "operator"
  },
  "command": {
    "name": "runtime.status",
    "args": {
      "runtime": "Hermes"
    }
  },
  "requested_authority": "read_only",
  "approval_required": false,
  "created_at": "YYYY-MM-DDTHH:MM:SSZ"
}
```

## Public-Safe Fields

- Use `env:` references for deployment-specific IDs.
- Use example identity labels.
- Use synthetic message IDs.
- Avoid raw message content unless the adapter explicitly sanitizes it.

## Required Validation

- Envelope version is supported.
- Channel is allowed for the requested command.
- Sender identity maps to a local role.
- Requested authority is within the sender role ceiling.
- Command type is allowlisted.
- Approval-required command types produce review artifacts.

