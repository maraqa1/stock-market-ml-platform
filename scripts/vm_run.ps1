param(
    [Parameter(Position = 0)]
    [string]$Command = "",
    [switch]$Pull,
    [switch]$Diagnostics,
    [string]$Tests = "",
    [string]$HostName = "37.27.4.125",
    [string]$User = "root",
    [string]$RemoteCwd = "/home/massa/stock-market-ml-platform",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\strategic_cascade_actions"
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $global:PSNativeCommandUseErrorActionPreference = $false
}

if (-not (Test-Path $KeyPath)) {
    throw "SSH key not found: $KeyPath"
}

$steps = New-Object System.Collections.Generic.List[string]
$steps.Add("set -euo pipefail")
$steps.Add("cd '$RemoteCwd'")
$steps.Add("echo vm_host:`$(hostname)")
$steps.Add("echo vm_cwd:`$(pwd)")
$steps.Add("echo vm_branch:`$(git branch --show-current)")
$steps.Add("echo vm_head:`$(git log -1 --oneline)")

if ($Pull) {
    $steps.Add("git pull --ff-only origin dev")
    $steps.Add("echo vm_head_after_pull:`$(git log -1 --oneline)")
}

if ($Tests.Trim()) {
    $steps.Add("PYTHONPATH=src /opt/jupyter-env/bin/python3 -m pytest $Tests")
}

if ($Diagnostics) {
    $steps.Add("PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_strategy_diagnostics.py")
    $steps.Add("PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_intraday_promotion_replay.py")
}

if ($Command.Trim()) {
    $steps.Add($Command)
}

$remoteScript = ($steps -join "`n") + "`n"
$logDir = Join-Path (Get-Location) "data\local_vm_logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "vm_run_$stamp.log"

Write-Host "vm_run_log: $logPath"
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript))
& ssh -i $KeyPath -o BatchMode=yes -o ConnectTimeout=10 "$User@$HostName" "printf '%s' '$encodedScript' | base64 -d | bash" 2>&1 | Tee-Object -FilePath $logPath
$code = $LASTEXITCODE
$ErrorActionPreference = $previousPreference
exit $code
