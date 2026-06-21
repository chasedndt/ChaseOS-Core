# runtime/memory/adapters/

Structured Layer C runtime profiles.

Each runtime gets:

```text
runtime/memory/adapters/<runtime_id>/profile.json
runtime/memory/adapters/<runtime_id>/identity-ledger.json
```

Profiles are behavioral memory: tendencies, strengths, known failure modes,
corrections, and confidence signals. They do not define authority. Authority
still comes from Gate policy, runtime registry records, role cards, workflow
manifests, and operator approval.

Identity ledgers are the longer-form behavioral record for a runtime:
doctrine adherence, correction history, drift signals, and evidence references.
They are advisory Layer C memory and do not raise trust tier, grant write scope,
or replace current vault truth.

Current formal ledger seeds:

```text
runtime/memory/adapters/claude/identity-ledger.json
runtime/memory/adapters/hermes/identity-ledger.json
runtime/memory/adapters/openclaw/identity-ledger.json
```
