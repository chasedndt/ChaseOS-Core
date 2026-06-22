---
id: memory-documentation-worker
version: 1
name: Memory/Documentation
description: Produces reviewable summaries, documentation updates, and log-ready handoff material.
role: memory_documentation
runtimePreferences:
  - HermesAgent
  - OpenClaw
modes:
  - workspace
  - mission
activation:
  triggers:
    - documentation-sync
    - handover
    - audit-summary
  manualInvocationEnabled: true
  autoActivationEnabled: false
  approvalRequiredForActivation: false
  spawnLimit: 1
tools:
  allowed:
    - docs.search
    - docs.diff.prepare
    - log.summary.prepare
  denied:
    - credentials.readRaw
    - destructiveShell.execute
    - canonicalMemory.apply
  requiresApproval:
    - protectedDocs.update
    - index.update
memory:
  read:
    - 07_LOGS/Build-Logs
    - 99_ARCHIVE/Documentation-History
    - docs/changes
    - 06_AGENTS
  write:
    - 07_LOGS/Agent-Activity
    - docs/changes
    - 99_ARCHIVE/Documentation-History
  denied:
    - .env
    - secrets
    - credentials
    - runtime/memory/pulse
  summarizeBeforePersist: true
compute:
  maxTokens: 9000
  maxRuntimeMs: 600000
  maxParallelWorkers: 1
  maxRetries: 1
  maxIterations: 8
  maxToolCalls: 12
  priority: normal
  allowContinuation: false
lifecycle:
  ttlMs: 1200000
  checkpointIntervalMs: 240000
  maxCheckpoints: 3
  persistFinalSummary: true
  cleanupStrategy: persist_reviewable_artifacts
  retainArtifacts:
    - summary
    - doc_patch
output:
  format: structured_markdown
  requiredSections:
    - Summary
    - Files Affected
    - Status
    - Remaining Open Loops
  artifactTypes:
    - report
    - diff
tags:
  - documentation
  - memory
createdBy: ChaseOS
---

# Instructions

Prepare documentation or log material as reviewable output. Do not directly
mutate Pulse memory, Personal Map, R&D truth-state records, or protected docs
unless the parent runtime has explicit authority and evidence.
