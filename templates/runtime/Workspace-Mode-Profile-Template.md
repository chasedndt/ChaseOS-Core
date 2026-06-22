---
type: template
title: Workspace Mode Profile Template
status: active
created: 2026-05-13
---

# Workspace Mode Profile Template

Copy the YAML block into `workspace-mode.yaml` or into Project-OS frontmatter.

Canonical/protected writes require approval. Generated ideas are not canonical truth. Runtime adapter ceilings do not grant authority by themselves.

```yaml
# Required. Stable machine id for this workspace.
workspace_id: example-workspace

# Required. Human-readable name.
workspace_name: Example Workspace

# Required. One of:
# personal_os | study_research | founder_venture | business_ops | runtime_agent_ops | unknown
workspace_mode: unknown

# Required. One or two sentences explaining the workspace purpose.
description: >
  Replace with the workspace purpose and operating context.

# Required. Domains this workspace primarily serves.
primary_domains:
  - Example Domain

# Required. Files that count as canonical/current state for this workspace.
canonical_state_files:
  - docs/framework-home/Now.md

# Required. Files agents should read first when working here.
required_read_order:
  - README.md
  - PROJECT_FOUNDATION.md
  - docs/framework-home/Now.md

# Required. Preserve the ChaseOS six-class taxonomy.
allowed_knowledge_classes:
  - user-origin
  - source-derived
  - synthesized
  - generated-ideas
  - system-operational
  - canonical-state

# Required. Output classes normally produced in this workspace.
default_output_classes:
  - proposal
  - generated-idea

# Required. AOR/workflow ids allowed for this workspace.
allowed_workflows: []

# Required. Ceilings only, not permission grants.
runtime_adapter_ceiling:
  claude: tier-3
  codex: blocked
  openclaw: blocked
  hermes: blocked

# Required. Approval posture.
approval_rules:
  canonical_state_write: explicit_user_approval_required
  generated_idea_creation: allowed_with_label
  generated_idea_endorsement: human_only
  source_promotion: gate_required
  protected_file_write: explicit_per_file_approval_required
  shell_execution: blocked_by_default
  external_connector_action: blocked_by_default

# Required. Declarative graph hygiene expectations.
graph_rules:
  update_domain_index_on_promotion: true
  backlinks_required_for_durable_notes: true
  orphan_notes_flagged: true

# Required. Paths that should trigger escalation before write.
protected_paths:
  - .env
  - secrets/
  - credentials/
  - docs/framework-home/Now.md
  - ROADMAP.md
  - PROJECT_FOUNDATION.md

# Required. Default write targets for approved/session outputs.
default_write_targets:
  - docs/framework-logs/Build-Logs/
  - docs/framework-logs/Agent-Activity/
  - 99_ARCHIVE/Documentation-History/

# Required. Fail-closed escalation behavior.
escalation_rules:
  unknown_mode: stop_and_request_mode
  protected_write: require_explicit_approval
  external_action: require_explicit_approval
  runtime_authority_unclear: fail_closed
```

## Mode Examples

### personal_os

```yaml
workspace_mode: personal_os
primary_domains:
  - Personal OS
default_output_classes:
  - daily-review
  - generated-idea
runtime_adapter_ceiling:
  claude: tier-3
  codex: blocked
  openclaw: blocked
  hermes: blocked
```

### study_research

```yaml
workspace_mode: study_research
primary_domains:
  - University
  - Research
default_output_classes:
  - source-note
  - synthesis-note
  - study-guide
runtime_adapter_ceiling:
  claude: tier-3
  codex: blocked
  openclaw: blocked
  hermes: blocked
```

### founder_venture

```yaml
workspace_mode: founder_venture
primary_domains:
  - Product
  - Venture R&D
default_output_classes:
  - venture-brief
  - feature-spec
  - experiment-log
runtime_adapter_ceiling:
  claude: tier-2
  codex: tier-2
  openclaw: tier-2-bounded
  hermes: tier-2-bounded
```

### business_ops

```yaml
workspace_mode: business_ops
primary_domains:
  - Business Operations
default_output_classes:
  - sop-draft
  - workflow-map
  - approval-packet
runtime_adapter_ceiling:
  claude: tier-2
  codex: tier-3
  openclaw: tier-2-bounded
  hermes: tier-2-bounded
```

### runtime_agent_ops

```yaml
workspace_mode: runtime_agent_ops
primary_domains:
  - Runtime Governance
  - Agent Operations
default_output_classes:
  - build-log
  - agent-activity-log
  - audit-record
  - proposal
runtime_adapter_ceiling:
  claude: tier-2
  codex: tier-2
  openclaw: tier-2-bounded
  hermes: tier-2-bounded
```
