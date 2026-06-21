# Discord Channel Registry Example

Use this as a template for a private local Discord channel registry. Do not publish live channel IDs.

```yaml
version: 1
guild_ref: "env:DISCORD_GUILD_ID"
channels:
  operator:
    channel_ref: "env:DISCORD_OPERATOR_CHANNEL_ID"
    purpose: "operator commands and status requests"
    allowed_authority:
      - read_only
      - proposal
  review:
    channel_ref: "env:DISCORD_REVIEW_CHANNEL_ID"
    purpose: "approval requests and review packets"
    allowed_authority:
      - approval_review
  runtime_status:
    channel_ref: "env:DISCORD_RUNTIME_STATUS_CHANNEL_ID"
    purpose: "runtime heartbeat and task status"
    allowed_authority:
      - notification_only
```

## Rules

- Keep real IDs in private config only.
- Keep public examples synthetic.
- Do not route write or host actions from general chat channels.
- Require a separate review channel for approval decisions.

