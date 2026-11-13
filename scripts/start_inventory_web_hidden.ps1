[CmdletBinding()]
param(
  [int]$Port = 0,
  [string]$BindHost = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $Root

# Keep the existing runtime and authentication configuration contract.
$RuntimeConfig = Join-Path $Root "config\runtime.ps1"
if (Test-Path -LiteralPath $RuntimeConfig) {
  . $RuntimeConfig
}

$AuthConfig = Join-Path $Root "config\auth.ps1"
if (Test-Path -LiteralPath $AuthConfig) {
  . $AuthConfig
}

if ($Port -le 0) {
  $Port = if ($env:PICKUP_WEB_PORT) { [int]$env:PICKUP_WEB_PORT } else { 8791 }
}
if ([string]::IsNullOrWhiteSpace($BindHost)) {
  $BindHost = if ($env:PICKUP_WEB_HOST) { $env:PICKUP_WEB_HOST } else { "0.0.0.0" }
}

$env:PICKUP_WEB_HOST = $BindHost
$env:PICKUP_WEB_PORT = [string]$Port

$NodeExe = $env:PICKUP_NODE_EXE
if ([string]::IsNullOrWhiteSpace($NodeExe)) {
  $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
  if ($NodeCommand) {
    $NodeExe = $NodeCommand.Source
  }
}
if ([string]::IsNullOrWhiteSpace($NodeExe)) {
  throw "Node.js was not found. Set PICKUP_NODE_EXE or add node.exe to PATH."
}

$ServerPath = Join-Path $Root "server.js"
if (-not (Test-Path -LiteralPath $ServerPath)) {
  throw "Missing server entry point: $ServerPath"
}

$LogDir = Join-Path $Root "output\inventory-server"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$StdoutLog = Join-Path $LogDir "server.out.log"
$StderrLog = Join-Path $LogDir "server.err.log"
$LauncherLog = Join-Path $LogDir "launcher.log"

function Write-LauncherLog {
  param([string]$Message)
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  Add-Content -LiteralPath $LauncherLog -Value $line -Encoding UTF8
}

Write-LauncherLog ("Launch requested: host={0}, port={1}" -f $BindHost, $Port)

# A listener on the configured port is the authoritative duplicate guard. It
# also protects against a manually started copy whose command line used a
# relative server.js path.
$Listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
  Select-Object -First 1
if ($Listener) {
  Write-LauncherLog ("Already listening on port {0}, PID={1}; no new process started." -f $Port, $Listener.OwningProcess)
  Write-Output ("Inventory web service is already running on port {0} (PID {1})." -f $Port, $Listener.OwningProcess)
  exit 0
}

# If the process exists but has not bound its port yet, avoid starting a
# second copy. Passing an absolute server path makes this check reliable for
# launches made by this script.
$Existing = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($ServerPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } |
  Select-Object -First 1
if ($Existing) {
  Write-LauncherLog ("Matching Node process already exists, PID={0}; no new process started." -f $Existing.ProcessId)
  Write-Output ("Inventory web service process is already starting (PID {0})." -f $Existing.ProcessId)
  exit 0
}

Write-LauncherLog ("Starting Node: {0} {1}" -f $NodeExe, $ServerPath)
try {
  $Process = Start-Process `
    -FilePath $NodeExe `
    -ArgumentList @($ServerPath) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru
} catch {
  Write-LauncherLog ("Start-Process failed: {0}" -f $_.Exception.Message)
  throw
}

$HealthUrl = "http://127.0.0.1:{0}/health" -f $Port
$Healthy = $false
for ($attempt = 1; $attempt -le 15; $attempt++) {
  Start-Sleep -Seconds 1
  if ($Process.HasExited) {
    $message = "Node exited during startup with code {0}. See {1} and {2}." -f $Process.ExitCode, $StdoutLog, $StderrLog
    Write-LauncherLog $message
    throw $message
  }
  try {
    $Response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
    if ($Response.StatusCode -eq 200) {
      $Healthy = $true
      break
    }
  } catch {
    # The listener can take a few seconds to bind; retry before declaring a failure.
  }
}

if (-not $Healthy) {
  $message = "Node process started (PID {0}) but health check failed: {1}. See {2} and {3}." -f $Process.Id, $HealthUrl, $StdoutLog, $StderrLog
  Write-LauncherLog $message
  throw $message
}

Write-LauncherLog ("Healthy: PID={0}, URL={1}" -f $Process.Id, $HealthUrl)
Write-Output ("Inventory web service started hidden (PID {0}). Health: {1}" -f $Process.Id, $HealthUrl)
