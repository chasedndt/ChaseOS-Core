$ErrorActionPreference = "Stop"

# ChaseOS coordination-watch Task Scheduler registration template.
# Copy this into private local config before replacing placeholders.

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
$LauncherPath = Join-Path $VaultRoot "runtime/lifecycle/bootstrap/$Runtime-coordination-watch-start.cmd"

$RegisterArgs = @(
    "/Create",
    "/SC", "ONLOGON",
    "/TN", $TaskName,
    "/TR", $LauncherPath,
    "/F"
)

$VerifyArgs = @("/Query", "/TN", $TaskName)
$RemoveArgs = @("/Delete", "/TN", $TaskName, "/F")

Write-Host "Requesting elevation for ChaseOS coordination-watch bootstrap registration..."
$Process = Start-Process -FilePath "schtasks.exe" -ArgumentList $RegisterArgs -Verb RunAs -Wait -PassThru -WindowStyle Hidden

Write-Host "Scheduler registration exit code:" $Process.ExitCode
Write-Host "Verifying Task Scheduler registration..."
schtasks.exe $VerifyArgs

Write-Host "To unregister later from an elevated PowerShell session, run:"
Write-Host (("schtasks.exe " + ($RemoveArgs -join " ")))

exit $Process.ExitCode

