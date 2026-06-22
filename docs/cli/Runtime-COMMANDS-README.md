# runtime/ — Commands README

> Human-facing guide to the current runtime-related shell and Python command surfaces visible from this repository.

---

## Why This Exists

ChaseOS now has enough runtime substrate that operators need a clear command summary.

This file is intentionally honest about the difference between:
- commands directly inspectable from this repo
- broader documented ChaseOS command surfaces
- future intended command contracts

---

## Canonical Operator Commands

The canonical ChaseOS CLI entrypoint is now `runtime.cli.main:main`, exposed through installed `chaseos` / `chase` and mirrored by the compatibility shims `chaseos.py` and `runtime/cli.py`.

Examples that are now directly invokable from this repo/environment:

```powershell
python -m runtime.cli.main runtime inventory --json
python -m runtime.cli.main runtime status --runtime all --json
python -m runtime.cli.main gate validate --json
python -m runtime.cli.main setup provider list --json
python -m runtime.cli.main setup discord validate --json
python chaseos.py runtime inventory --json
python runtime\cli.py runtime inventory --json
```

## Lower-Level / Subsystem Commands

### ChaseOS Gate stub
From repo root:

```powershell
python runtime\chaseos_gate.py validate
python runtime\chaseos_gate.py list
python runtime\chaseos_gate.py show openclaw
python runtime\chaseos_gate.py check-write openclaw docs/framework-logs/Agent-Activity/test.md
python runtime\chaseos_gate.py check-task openclaw operator-briefing
```

### Runtime state resolver
```powershell
python runtime\state\resolver.py
```

### Runtime CLI foothold
```powershell
python runtime\state\runtime_cli.py resolve
python runtime\state\runtime_cli.py status
python runtime\state\runtime_cli.py status --refresh
python runtime\state\runtime_cli.py status --refresh --json
```

---

## Broader ChaseOS Command Families

The README and architecture docs also refer to a broader `chaseos` command family, including examples like:
- `chaseos capture ...`
- `chaseos watch ...`
- `chaseos intake ...`
- `chaseos doctor`
- `chaseos test capture`
- `chaseos run <workflow>`

Many of those are now package-native and locally invokable through the canonical CLI. Others may still be at different maturity levels, so operators should still verify active implementation status before assuming every documented command family is complete.

---

## Canonical Runtime Commands

The canonical runtime commands are now:

```text
chaseos runtime resolve
chaseos runtime status
```

See:
- `runtime/state/CLI-README.md`
- `runtime/state/COMMAND-CONTRACT-README.md`
- `06_AGENTS/ChaseOS-Runtime-Command-Contract.md`

---

## Alignment with ChaseOS OS Direction

This command inventory matters because ChaseOS is moving toward a real operating-system surface with:
- inspectable runtime state
- bounded operator workflows
- explicit command families
- future local interfaces and gateway-adjacent surfaces

A command README helps bridge architecture and actual operator use.
