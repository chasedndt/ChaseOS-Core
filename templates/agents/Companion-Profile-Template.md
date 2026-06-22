---
type: companion-profile-template
title: Companion Profile Template
status: TEMPLATE / V0.1
created: 2026-05-13
---

# Companion Profile Template

```yaml
companion_id: example-companion
display_name: Example Companion
runtime_identity: example-runtime
short_description: One-line companion description.
role_summary: Read-only status and commentary role.
personality_preset: calm_status
tone_profile: Quiet status narration and governance-aware comments.
visual_mark:
  kind: abstract_runtime_mark
  token: E
  asset_path: ""
  final_brand_asset_required: false
border_style: neutral
animation_preset: none
status_states:
  - idle
  - selected
  - running
  - waiting_for_approval
  - blocked
  - warning
  - complete
  - unavailable
rarity:
  label: built-in
  cosmetic_only: true
  changes_capability: false
stats:
  clarity:
    value: 80
    cosmetic_only: true
    changes_capability: false
capability_summary: >
  Companion metadata does not grant runtime authority, routing, provider/model
  access, tools, permissions, memory, or writeback.
governance_boundary: Companion identity is not runtime authority.
memory_scope: No separate companion memory exists in v0.1.
routing_effect: none
permission_effect: none
current_status: available
allowed_effects:
  - visual_identity
  - profile_card_metadata
  - tone_preset
  - status_narration
  - read_only_runtime_card_display
  - non_authoritative_commentary
forbidden_effects:
  - runtime_routing_changes
  - model_provider_switching
  - memory_scope_changes
  - permission_changes
  - tool_access_changes
  - connector_access_changes
  - protected_file_access_changes
  - workflow_execution_changes
  - canonical_state_mutation
commentary_policy:
  classification: non_authoritative_commentary
  can_trigger_tools: false
  can_override_policy: false
  can_write_memory: false
  can_mutate_canonical_state: false
```

## Rule

This template is descriptive only. Filling it out must not grant runtime
authority, model/provider access, memory access, tools, connector access,
workflow execution, protected-file access, or canonical mutation.
