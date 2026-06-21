# Runtime Startup Controls

Runtime startup controls describe how a ChaseOS instance may start or supervise runtime workers such as Hermes and OpenClaw.

## Public-Safe Principle

Public Core may include startup templates, schemas, and examples. It must not include one operator's live Task Scheduler registration, local vault path, username, WSL distro details, log files, PID files, or credential references by value.

## Startup Control Types

- Manual daemon command.
- Windows Task Scheduler registration.
- Windows Startup folder launcher.
- systemd user service.
- LaunchAgent or launchd service.
- Container or process supervisor.

## Required Template Fields

- Runtime ID.
- Runtime display name.
- Vault root placeholder.
- Python or executable placeholder.
- Log path placeholder.
- State path placeholder.
- Startup trigger.
- Registration kind.
- Restart policy.
- Approval requirements.

## Approval Rules

Host startup mutation requires explicit operator approval because it changes behavior outside the repository. Public templates may describe how to register startup tasks, but live registration must be local and reviewed.

## Public Template Set

- Runtime lifecycle YAML example.
- Hermes lifecycle YAML example.
- OpenClaw lifecycle YAML example.
- Coordination watch start command template.
- Task Scheduler handoff template.
- Reboot verify template.

