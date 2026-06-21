# Discord Control Plane

Discord can be used as an operator-facing transport for ChaseOS commands, notifications, and review requests. Discord is a control-plane surface, not the source of truth.

## Purpose

- Receive bounded operator commands.
- Deliver status updates and review prompts.
- Route approved command envelopes into ChaseOS runtime queues.
- Preserve channel, identity, and approval boundaries.

## Boundary

Discord must not own canonical memory, project truth, runtime state, credentials, or approval authority. ChaseOS owns those records locally. Discord messages are inputs, outputs, or references.

## Required Public-Safe Components

- Command envelope schema.
- Channel registry example.
- Runtime identity map example.
- Credential boundary SOP.
- Agent Bus task packet example.
- Runtime profile template.

## Forbidden By Default

- Discord bot tokens in repository files.
- Webhook URLs in repository files.
- Live channel IDs in public examples.
- Live user IDs in public examples.
- Unapproved command execution.
- Direct canonical writeback from Discord text.

## Recommended Flow

1. Operator sends a command in an allowed channel.
2. Discord adapter converts the message into a command envelope.
3. ChaseOS validates channel, identity, task type, and authority.
4. Valid commands become task packets or approval requests.
5. Runtime workers act only within their declared permissions.
6. Results are written locally and summarized back to Discord if allowed.

## Public Example Rules

Use placeholders for all deployment-specific values:

- `DISCORD_BOT_CREDENTIAL_REF`
- `DISCORD_GUILD_ID`
- `DISCORD_OPERATOR_CHANNEL_ID`
- `DISCORD_REVIEW_CHANNEL_ID`
- `DISCORD_RUNTIME_STATUS_CHANNEL_ID`

Do not publish values for those variables.
