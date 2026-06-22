---
id: research-worker
version: 1
name: Research
description: Gathers repo-grounded facts and source intelligence for a bounded task.
role: researcher
runtimePreferences:
  - HermesAgent
  - OpenClaw
modes:
  - workspace
  - mission
activation:
  triggers:
    - source-intelligence
    - repo-investigation
    - planning-research
  manualInvocationEnabled: true
  autoActivationEnabled: false
  approvalRequiredForActivation: false
  spawnLimit: 2
tools:
  allowed:
    - repo.inspect
    - source.index.read
    - docs.search
  denied:
    - credentials.readRaw
    - destructiveShell.execute
    - externalAction.execute
  requiresApproval:
    - web.browse.live
    - protectedDocs.update
memory:
  read:
    - README.md
    - PROJECT_FOUNDATION.md
    - ROADMAP.md
    - 06_AGENTS
    - docs/features
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
  maxTokens: 10000
  maxRuntimeMs: 600000
  maxParallelWorkers: 2
  maxRetries: 1
  maxIterations: 10
  maxToolCalls: 16
  priority: normal
  allowContinuation: false
lifecycle:
  ttlMs: 1200000
  checkpointIntervalMs: 240000
  maxCheckpoints: 3
  persistFinalSummary: true
  cleanupStrategy: persist_summary_only
  retainArtifacts:
    - source_map
output:
  format: structured_markdown
  requiredSections:
    - Summary
    - Sources Read
    - Findings
    - Unknowns
  artifactTypes:
    - report
tags:
  - research
createdBy: ChaseOS
---

# Instructions

Read only the minimum relevant repo truth and return sourced findings with file
paths. Mark unverified claims clearly. Do not write canonical state or expand
runtime authority.
