param(
  [string]$VaultRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path,
  [string]$CodexBinary = "codex",
  [int]$IntervalSeconds = 30,
  [int]$TimeoutSeconds = 900
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Push-Location $VaultRoot
try {
  python -m chaseos agent-bus codex-daemon --readiness --codex-binary $CodexBinary --vault-root $VaultRoot --json
  if ($LASTEXITCODE -ne 0) {
    throw "Codex daemon readiness failed. Install Codex CLI, set CHASEOS_CODEX_BINARY, or pass -CodexBinary."
  }

  python -m chaseos agent-bus codex-daemon --interval $IntervalSeconds --executor codex --codex-binary $CodexBinary --timeout-seconds $TimeoutSeconds --vault-root $VaultRoot
}
finally {
  Pop-Location
}
