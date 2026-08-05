# ChaseOS Core Quickstart

ChaseOS Core is a reusable operating-system framework for personal knowledge, projects, governed agent runtimes, and repeatable workflows. This quickstart takes a new fork from an empty checkout to a safe first local instance.

## First Principles

- Start with Core as framework code and documentation.
- Keep personal/private data in a separate private instance.
- Do not commit credentials, raw inputs, private logs, runtime memory, approval queues, or live agent-bus state.
- Use examples and templates as scaffolds; replace them with your own private context outside the public Core tree.
- Promote durable truth through a review gate rather than direct agent writeback.

## First 10 Minutes After Fork

### 1. Clone or fork Core

```bash
git clone https://github.com/<owner-or-upstream>/ChaseOS-Core.git chaseos-core
cd chaseos-core
```

If you forked on GitHub, use your fork URL. Keep upstream as the reusable framework source.

### 2. Read the boundary docs

Start here before adding personal content:

```text
CORE_MANIFEST.md
docs/getting-started/Core-vs-Personal.md
FORKING.md
SECURITY.md
```

The key rule: **Core is public framework; Personal is private instance.**

### 3. Create a private personal instance

Create a separate folder outside the public Core checkout, for example:

```bash
mkdir -p ../my-chaseos-instance
```

Copy only neutral templates and examples you intentionally want to use:

```bash
cp -R 05_TEMPLATES ../my-chaseos-instance/05_TEMPLATES
cp SOUL.template.md ../my-chaseos-instance/SOUL.md
```

Then populate the private instance with your own identity, domains, project files, notes, and operating dashboard. Do **not** put credentials or live runtime state in the public Core repo.

### 4. Install the lean Core CLI

Use a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows PowerShell equivalent:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

### 5. Verify the CLI

```bash
chaseos version
chaseos doctor --vault-root . --json
```

`doctor` should return a JSON envelope with `ok: true` when the Core runtime blocks import and the current checkout has the expected vault/framework shape.

### 6. Run a safe sample capture

Use explicit local input only. This writes through the normal capture/quarantine path under the selected vault root:

```bash
printf 'Synthetic source note for my first Core capture.\n' > /tmp/chaseos-sample-source.txt
chaseos capture file /tmp/chaseos-sample-source.txt --title "Sample source" --vault-root . --json
```

Optional local image-text posture check:

```bash
chaseos capture image-text-status --json
```

`capture image-text` is local-only and explicit-file-only. It does not enable cloud OCR, ambient screen capture, browser-profile access, provider calls, SIC ingestion, or canonical promotion.

### 7. Initialize the local Connections registry

Inspect bundled provider manifests:

```bash
chaseos connections providers --json
```

Initialize the local-first SQLite registry in your chosen vault root:

```bash
chaseos connections init --vault-root . --json
chaseos connections list --vault-root . --json
```

Seed a disconnected placeholder connection, for example:

```bash
chaseos connections seed local_files --vault-root . --json
```

This does **not** authenticate providers, fetch private data, send messages, or enable ambient agents. It only records local registry state and provider capability metadata.

### 8. Inspect read-only commercial scaffolding

```bash
chaseos commerce catalog --json
chaseos commerce flags --json
```

These commands expose Core's product/workflow-pack scaffolding. They are not production billing by themselves.

### 9. Run the repo-safe secret audit before publishing

Before pushing a public fork, run:

```bash
python - <<'PY'
from runtime.repo_secret_audit import audit_repo_secrets, format_repo_secret_audit
report = audit_repo_secrets('.')
print(format_repo_secret_audit(report))
raise SystemExit(0 if report.get('ok') else 1)
PY
```

A safe result should report:

```text
high_confidence_secret_count: 0
raw_secret_values_emitted: False
```

Also manually review for personal names, local paths, private project details, real logs, and account-specific identifiers. Secret scanning does not replace human review.

## First Safe Workflow

Begin with a docs-only workflow:

1. create a synthetic project note in your private instance;
2. log a decision using the templates;
3. review it through the approval/review pattern;
4. only then experiment with capture, schedules, connections, or bounded workflow dry-runs.

Do not start with browser automation, shell mutation, credentials, publication, paid APIs, or connector automation.

## What To Keep Out Of Public Core

Never commit:

- `.env`, API keys, tokens, private keys, passwords, cookies, OAuth grants, service-account JSON;
- real approval queues, runtime logs, agent-bus state, or workflow outputs;
- personal identity files, project history, domain knowledge, customer/client data, or private research;
- machine-local paths such as `C:\Users\<name>\...` or `/home/<name>/...`;
- provider-specific deployment state or live account bindings.

Use `.example.md`, `.example.yaml`, `.template.md`, or neutral placeholders when demonstrating a pattern.
