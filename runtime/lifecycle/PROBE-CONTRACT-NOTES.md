# Runtime Lifecycle Probe Contract Notes

## Why candidate ports are explicit

On a multi-runtime machine, health probes should not hide their candidate port assumptions inside opaque URL lists.

Making candidate ports explicit helps the operator understand:
- which ports ChaseOS expects each runtime to use
- which ports are stable defaults
- which ports are provisional scan targets pending live validation

## Current runtime probe assumptions

### OpenClaw
- probe label: `openclaw-loopback-dashboard`
- candidate ports:
  - `18789`
- expected transport:
  - local loopback HTTP dashboard

### Hermes
- probe label: `hermes-local-http-candidate-scan`
- candidate ports:
  - `18790`
  - `18791`
- expected transport:
  - local HTTP endpoint, likely crossing Windows/WSL runtime boundaries

## Product-shape meaning

This is part of ChaseOS becoming an operator-facing runtime monitor.
A good monitor does not just say whether something is healthy. It also shows what it expected to probe.
