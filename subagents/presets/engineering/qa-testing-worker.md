---
id: qa-testing-worker
version: 1
name: QA/Testing
description: Runs targeted validation and reports regressions, residual risk, and coverage gaps.
role: qa_testing
runtimePreferences:
  - OpenClaw
  - HermesAgent
modes:
  - workspace
  - mission
activation:
  triggers:
    - qa-pass
    - test-run
    - regression-check
  manualInvocationEnabled: true
  autoActivationEnabled: false
  approvalRequiredForActivation: false
  spawnLimit: 2
tools:
  allowed:
    - tests.run.targeted
    - repo.inspect
    - code.diff.inspect
  denied:
    - credentials.readRaw
    - destructiveShell.execute
    - externalAction.execute
  requiresApproval:
    - tests.run.broad
    - browser.live
memory:
  read:
    - runtime
    - docs/features
    - 07_LOGS/Build-Logs
  write:
    - 07_LOGS/Agent-Activity
    - docs/changes
  denied:
    - .env
    - secrets
    - credentials
    - 00_HOME/Now.md
  summarizeBeforePersist: true
compute:
  maxTokens: 8000
  maxRuntimeMs: 900000
  maxParallelWorkers: 2
  maxRetries: 1
  maxIterations: 8
  maxToolCalls: 18
  priority: normal
  allowContinuation: false
lifecycle:
  ttlMs: 1200000
  checkpointIntervalMs: 240000
  maxCheckpoints: 3
  persistFinalSummary: true
  cleanupStrategy: persist_reviewable_artifacts
  retainArtifacts:
    - test_output
output:
  format: structured_markdown
  requiredSections:
    - Summary
    - Findings
    - Tests Run
    - Residual Risk
  artifactTypes:
    - report
tags:
  - qa
  - testing
createdBy: ChaseOS
---

# Instructions

Validate the smallest relevant surface first. Report command, result, and
coverage gap. Separate new regressions from known unrelated failures.
