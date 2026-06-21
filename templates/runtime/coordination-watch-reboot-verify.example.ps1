$ErrorActionPreference = "Stop"

# ChaseOS coordination-watch reboot verification template.

$VaultRoot = $env:CHASEOS_VAULT
$Runtime = $env:CHASEOS_RUNTIME

if ([string]::IsNullOrWhiteSpace($VaultRoot)) {
    throw "CHASEOS_VAULT is required."
}

if ([string]::IsNullOrWhiteSpace($Runtime)) {
    throw "CHASEOS_RUNTIME is required. Example: hermes or openclaw."
}

$RuntimeTitle = (Get-Culture).TextInfo.ToTitleCase($Runtime)
$TaskName = "ChaseOS-$RuntimeTitle-Coordination-Watch"
$ResultPath = Join-Path $VaultRoot "runtime/lifecycle/run/$Runtime-coordination-watch-reboot-verify-result.json"

$Task = schtasks.exe /Query /TN $TaskName /FO LIST 2>&1
$Status = if ($LASTEXITCODE -eq 0) { "registered" } else { "missing" }

$Result = [ordered]@{
    runtime = $Runtime
    task_name = $TaskName
    status = $Status
    checked_at = (Get-Date).ToString("o")
}

$Result | ConvertTo-Json -Depth 5 | Set-Content -Path $ResultPath -Encoding UTF8
Write-Host "Wrote reboot verification result to $ResultPath"

