# Security Policy — ChaseOS Core

## Reporting a vulnerability

Report security issues privately to security@chaseos.ai. Do not open a public issue
for an exploitable vulnerability. Include affected commit, reproduction, and impact
(redact secrets). We aim to acknowledge within a few working days and appreciate
coordinated disclosure.

## Scope

In scope: the code in this repository (Agent Bus, Schedule layer, and future Core
modules). Credential handling must be environment-variable based; secret leakage to
logs or artifacts is in scope. Out of scope: the separate proprietary ChaseOS
Studio/Cloud/Control Kernel, and issues requiring prior control of the host.

## Supported versions

Pre-1.0: only the latest `main` is supported. Pin a commit for reproducibility.
