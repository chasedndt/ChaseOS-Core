# runtime/lifecycle/ — Health README

> Human-facing guide for the first runtime lifecycle health-check foothold.

---

## What This Is

This is the first operational command surface built on top of the lifecycle layer.

It does not yet start or stop runtimes.
It reads the machine-readable lifecycle record and runs the declared health command.

That makes it the safest first lifecycle command to implement.

---

## Manual Usage

From repo root:

```powershell
python runtime\lifecycle\health_cli.py openclaw
python runtime\lifecycle\health_cli.py openclaw --json
python runtime\lifecycle\health_cli.py openclaw --timeout 5 --json
python runtime\lifecycle\health_probe_test.py openclaw --json
python runtime\lifecycle\health_cli.py hermes
```

---

## What It Uses

The command reads:
- `runtime/lifecycle/openclaw.lifecycle.yaml`
- `runtime/lifecycle/hermes.lifecycle.yaml`

and executes the declared `health.command` field.

---

## Why This Matters

This is the first point where the lifecycle layer starts becoming operational rather than only structural.

It is still a foothold, but it proves the path from:
- lifecycle contract
- machine-readable lifecycle record
- operator command surface

---

## Important Boundary

A health check is not the same as lifecycle control.

Current scope:
- read lifecycle record
- run health command
- report healthy/unhealthy

Not yet in scope:
- start
- stop
- restart

Current implementation caveat:
- some runtime-native health commands may hang or behave like interactive/service-status commands rather than returning a fast clean machine result
- lifecycle health therefore needs bounded timeout behavior and runtime-specific probe selection
- the current foothold now supports an explicit timeout flag
- direct local probe testing is available through `health_probe_test.py` so root-cause checks do not have to depend on the promoted command surface or this chat execution lane

---

## Alignment with the Overall ChaseOS OS

This helps ChaseOS evolve from runtime inspection only toward runtime lifecycle ownership in a safe order:
1. inspect state
2. inspect health
3. later control lifecycle

That is the right order for a bounded operating system.
