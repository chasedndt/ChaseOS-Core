# Credential Boundaries SOP

## Purpose

Keep credential values out of public Core and out of routine generated artifacts.

## Rules

- Do not store credential values in notes or templates.
- Use environment-specific secret managers or local configuration outside Core.
- Redact credential-bearing material before export.
- Treat logs and screenshots as review-required when credentials may be visible.

## Review

Before publication, scan for local paths, credential-like keys, live identifiers, and private runtime state.
