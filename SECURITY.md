# Security Policy

ChaseOS Core is a public framework. Do not report or publish private instance secrets, credentials, live runtime queues, or personal operator data in issues or examples.

## Supported Security Boundaries

- Approval-gated writes.
- Explicit runtime authority ceilings.
- Quarantine for untrusted inputs.
- Public/private Core export scanning.
- Credential exclusion by default.

## Supported Versions

Core is pre-1.0 and alpha. Security fixes are applied to `main`; there are no maintained
backport branches yet.

| Version | Supported |
|---|---|
| `main` | Yes |
| `0.1.x` tags | Best effort |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security reports.**

Use GitHub's private vulnerability reporting for this repository
([Security → Report a vulnerability](https://github.com/chasedndt/ChaseOS-Core/security/advisories/new)),
which keeps the report confidential until a fix is available.

When reporting, include:

- the affected public Core contract or module;
- reproduction steps using non-private fixtures only;
- the expected safety behavior versus what actually happened;
- impact assessment (what authority boundary is crossed).

Do not include real credentials, tokens, or private vault contents in a report.

### What to expect

- **Acknowledgement:** within 5 business days.
- **Initial assessment:** within 10 business days.
- **Disclosure:** coordinated once a fix is available. Reporters are credited in the
  advisory unless they ask otherwise.

Because Core is a framework rather than a hosted service, findings that depend entirely on a
private deployment's own configuration (credentials, provider bindings, local policy) are
usually deployment issues rather than Core vulnerabilities — but report them anyway if a
Core default made the unsafe configuration likely.
