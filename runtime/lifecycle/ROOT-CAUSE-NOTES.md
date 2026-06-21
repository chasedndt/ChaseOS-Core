# Runtime Lifecycle Health Root Cause Notes

## Current Working Diagnosis
The repeated hanging behavior is most likely caused by the original OpenClaw lifecycle health primitive:

```powershell
openclaw gateway status
```

That command appears too heavy or too long-running in this environment to function as a bounded health foothold for ChaseOS lifecycle checks.

## Why that diagnosis is credible
- The promoted command surface was normalized and then simplified.
- Timeout handling was added at the lifecycle CLI level.
- An extra Python subprocess hop was removed from `chaseos.py` for health checks.
- Even after those improvements, the health path still behaved badly through live execution.
- That strongly suggests the underlying health primitive, not just the wrapper shape, is the weak link.

## Resolution Direction
Prefer the cheapest honest health probe per runtime.

For OpenClaw, the healthier design is:
- use a loopback HTTP probe for health
- keep heavier service-status commands for operator debugging, not routine health checks

## Added local validation seam
Use `runtime/lifecycle/health_probe_test.py` to test the lifecycle health contract directly without going through the promoted top-level command surface.

Example:
```powershell
python runtime\lifecycle\health_probe_test.py openclaw --json
```

## Next likely refinements
- add clearer error reporting for HTTP probe failures
- allow per-runtime probe metadata beyond command-only assumptions
- keep operator-facing docs honest about health vs debug/status surfaces
