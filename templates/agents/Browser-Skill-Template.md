schema_version: 0.1
skill_id: <domain.intent>
domain: <domain>
intent: <short intent>
status: draft
mode: shadow
account_required: false
credentials_required: false
canonical_writeback: false

allowed_domains:
  - <origin>

inputs_schema: {}
outputs_schema: {}

preconditions:
  - isolated disposable browser profile
  - no credentials

steps:
  - step_id: <step-id>
    action: <navigate|wait_for|click_selector|drag|verify>
    target: <target>
    coordinate_strategy: selector

selectors: {}
fallbacks: []
wait_conditions: []
verification: {}

secret_policy:
  credentials: forbidden
  cookies: forbidden
  session_tokens: forbidden
  browser_profile_state: forbidden
  local_storage: forbidden
  allowed_secret_material: none

source_runs: []
approval_status: draft
risk_level: low
last_verified: null
