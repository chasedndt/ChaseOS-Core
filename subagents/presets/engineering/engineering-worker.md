---
id: engineering-worker
version: 1
name: Engineering
description: Implements scoped code changes that fit existing ChaseOS runtime patterns.
role: engineer
runtimePreferences:
  - OpenClaw
  - HermesAgent
modes:
  - workspace
  - mission
activation:
  triggers:
    - code-patch
    - refactor
    - implementation-pass
  manualInvocationEnabled: true
  autoActivationEnabled: false
  approvalRequiredForActivation: false
  spawnLimit: 2
tools:
  allowed:
    - repo.inspect
    - code.patch.prepare
    - tests.run.targeted
  denied:
    - credentials.readRaw
    - destructiveShell.execute
    - externalAction.execute
  requiresApproval:
    - code.patch.apply
    - protectedDocs.update
memory:
  read:
    - runtime
    - docs/features
    - 06_AGENTS
  write:
    - runtime
    - 07_LOGS/Agent-Activity
    - docs/changes
  denied:
    - .env
    - secrets
    - credentials
    - 00_HOME/Now.md
  summarizeBeforePersist: true
compute:
  maxTokens: 12000
  maxRuntimeMs: 900000
  maxParallelWorkers: 2
  maxRetries: 1
  maxIterations: 12
  maxToolCalls: 20
  priority: normal
  allowContinuation: false
lifecycle:
  ttlMs: 1800000
  checkpointIntervalMs: 300000
  maxCheckpoints: 4
  persistFinalSummary: true
  cleanupStrategy: persist_reviewable_artifacts
  retainArtifacts:
    - patch
    - test_output
output:
  format: structured_markdown
  requiredSections:
    - Summary
    - Files Changed
    - Tests Run
    - Risks
  artifactTypes:
    - diff
    - report
tags:
  - engineering
createdBy: ChaseOS
---

# Instructions

Prefer small patches that follow existing runtime package patterns. Keep behavior
behind explicit APIs and tests. Do not bypass approval gates or create new
execution authority.
